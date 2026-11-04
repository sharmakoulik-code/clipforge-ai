import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.models import Clip, ClipRenderVersion, Project, Transcript
from app.processing.pipeline import ProcessingFailure, run_processing_job
from app.storage.local import storage
from app.video.captions import generate_ass
from app.video.render import render_clip
from app.video.vision import track_speakers_and_crops
from app.workers.celery_app import celery

logger = logging.getLogger(__name__)


@celery.task(name="app.workers.tasks.prepare_uploaded_project", bind=True, max_retries=3)
def prepare_uploaded_project(self, job_id: str) -> None:
    """Run the resumable AI/video pipeline from persisted checkpoints."""
    try:
        run_processing_job(job_id)
    except ProcessingFailure as exc:
        if exc.retryable and self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=min(60, 5 * (2 ** self.request.retries)))
        logger.error("Non-retryable processing failure job_id=%s code=%s", job_id, exc.code)
    except Exception as exc:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=min(60, 5 * (2 ** self.request.retries)))
        logger.exception("Processing retries exhausted job_id=%s", job_id)


@celery.task(name="app.workers.tasks.render_edited_clip", bind=True, max_retries=2)
def render_edited_clip(self, clip_id: str) -> None:
    db = SessionLocal()
    try:
        clip = db.get(Clip, clip_id)
        if clip is None:
            return
        project = db.get(Project, clip.project_id)
        transcript = db.get(Transcript, clip.transcript_id) if clip.transcript_id else None
        if not project or not project.source_video_key or not transcript or transcript.status != "completed":
            raise RuntimeError("Clip source video or saved transcript is unavailable for rendering.")
        source = storage.resolve(project.source_video_key)
        version = (db.query(ClipRenderVersion).filter_by(clip_id=clip.id).order_by(ClipRenderVersion.version.desc()).first())
        version_number = (version.version if version else 0) + 1
        base = f"artifacts/{project.id}/clips/{clip.id}-v{version_number}"
        video_key, thumb_key, preview_key = f"{base}.mp4", f"artifacts/{project.id}/thumbnails/{clip.id}-v{version_number}.jpg", f"artifacts/{project.id}/previews/{clip.id}-v{version_number}.mp4"
        crop = track_speakers_and_crops(source, float(clip.start_seconds), float(clip.end_seconds))
        crop_key = f"artifacts/{project.id}/crop/{clip.id}-v{version_number}.json"
        crop_path = storage.resolve(crop_key)
        crop_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        crop_path.write_text(json.dumps(crop, separators=(",", ":")), encoding="utf-8")
        storage.commit(crop_key)
        metadata = dict(clip.metadata_json or {})
        caption_key = f"artifacts/{project.id}/captions/{clip.id}-v{version_number}.ass"
        caption_path = storage.resolve(caption_key)
        style = metadata.get("caption_style", "creator")
        options = metadata.get("caption_options", {})
        generate_ass({"segments": transcript.segments or []}, caption_path, float(clip.start_seconds), float(clip.end_seconds), style, options)
        storage.commit(caption_key)
        output = storage.resolve(video_key)
        thumbnail = storage.resolve(thumb_key)
        preview = storage.resolve(preview_key)
        result = render_clip(source, output, float(clip.start_seconds), float(clip.end_seconds), crop, caption_path, thumbnail, preview)
        storage.commit(video_key)
        storage.commit(thumb_key)
        storage.commit(preview_key)
        db.add(ClipRenderVersion(clip_id=clip.id, version=version_number, output_key=video_key, render_settings={"width": settings.render_width, "height": settings.render_height, "fps": settings.render_fps, "bitrate": settings.render_video_bitrate}, status="completed", validation_data=result))
        metadata.update({"thumbnail_key": thumb_key, "preview_key": preview_key, "caption_key": caption_key, "crop_key": crop_key, "render_validation": result, "duration_seconds": result["duration_seconds"]})
        clip.metadata_json = metadata
        clip.output_video_key = video_key
        clip.status = "completed"
        db.commit()
    except Exception as exc:
        db.rollback()
        clip = db.get(Clip, clip_id)
        if clip:
            clip.status = "needs_render" if clip.output_video_key else "failed"
            db.commit()
        logger.exception("Edited clip render failed clip_id=%s", clip_id)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=5 * (self.request.retries + 1))
    finally:
        db.close()
