import json
import logging
import random
from threading import Event, Thread
from datetime import datetime, timezone
from pathlib import Path

from app.billing.credits import actual_job_credits, refund_job, settle_job
from app.billing.plans import get_user_plan
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.models import Clip, ClipRenderVersion, ProcessingJob, Project, Transcript
from app.processing.pipeline import ProcessingFailure, run_processing_job
from app.storage.local import storage
from app.video.captions import generate_ass
from app.video.render import render_clip
from app.video.vision import manual_crop_plan, track_speakers_and_crops
from app.workers.celery_app import celery

logger = logging.getLogger(__name__)


def _worker_heartbeat(job_id: str, worker_id: str | None, task_id: str | None, retry_count: int | None = None,
                      dead_letter: bool = False) -> None:
    db = SessionLocal()
    try:
        job = db.get(ProcessingJob, job_id)
        if not job:
            return
        job.worker_id = (worker_id or "unknown")[:160]
        job.worker_task_id = (task_id or "")[:160] or None
        job.worker_heartbeat_at = datetime.now(timezone.utc)
        if retry_count is not None:
            job.retry_count = max(job.retry_count, retry_count)
        if dead_letter:
            job.dead_lettered_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()


def _start_heartbeat(job_id: str, worker_id: str | None, task_id: str | None, retry_count: int = 0) -> tuple[Event, Thread]:
    stop = Event()

    def pulse() -> None:
        while not stop.wait(30):
            try:
                _worker_heartbeat(job_id, worker_id, task_id, retry_count)
            except Exception:
                logger.exception("Could not refresh worker heartbeat job_id=%s", job_id)

    thread = Thread(target=pulse, name=f"clipforge-heartbeat-{job_id[:8]}", daemon=True)
    thread.start()
    return stop, thread


def _stop_heartbeat(pulse: tuple[Event, Thread] | None) -> None:
    if pulse is None:
        return
    pulse[0].set()
    pulse[1].join(timeout=2)


def _finalize_job_credits(job_id: str) -> None:
    db = SessionLocal()
    try:
        job = db.get(ProcessingJob, job_id)
        if not job:
            return
        if job.status == "completed":
            settle_job(db, job.id, actual_job_credits(db, job))
        elif job.status == "failed":
            refund_job(db, job.id, job.error_message or "Processing failed")
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Credit settlement failed job_id=%s", job_id)
        raise
    finally:
        db.close()


@celery.task(name="app.workers.tasks.prepare_uploaded_project", bind=True, max_retries=settings.processing_task_max_retries,
            soft_time_limit=settings.processing_job_timeout_seconds,
            time_limit=settings.processing_job_timeout_seconds + 300)
def prepare_uploaded_project(self, job_id: str) -> None:
    """Run the resumable AI/video pipeline from persisted checkpoints."""
    worker_id, task_id = getattr(self.request, "hostname", None), getattr(self.request, "id", None)
    _worker_heartbeat(job_id, worker_id, task_id, getattr(self.request, "retries", 0))
    pulse = _start_heartbeat(job_id, worker_id, task_id, getattr(self.request, "retries", 0))
    try:
        run_processing_job(job_id)
        _finalize_job_credits(job_id)
    except ProcessingFailure as exc:
        retrying = exc.retryable and self.request.retries < self.max_retries
        _worker_heartbeat(job_id, worker_id, task_id, self.request.retries + (1 if retrying else 0), dead_letter=not retrying)
        if retrying:
            delay = min(900, 5 * (2 ** self.request.retries)) * random.uniform(0.8, 1.2)
            raise self.retry(exc=exc, countdown=delay)
        _finalize_job_credits(job_id)
        logger.error("Final processing failure job_id=%s code=%s retryable=%s", job_id, exc.code, exc.retryable)
    except Exception as exc:
        retrying = self.request.retries < self.max_retries
        _worker_heartbeat(job_id, worker_id, task_id, self.request.retries + (1 if retrying else 0), dead_letter=not retrying)
        if retrying:
            delay = min(900, 5 * (2 ** self.request.retries)) * random.uniform(0.8, 1.2)
            raise self.retry(exc=exc, countdown=delay)
        _finalize_job_credits(job_id)
        logger.exception("Processing retries exhausted job_id=%s", job_id)
    finally:
        _stop_heartbeat(pulse)


@celery.task(name="app.workers.tasks.render_edited_clip", bind=True, max_retries=2)
def render_edited_clip(self, clip_id: str, job_id: str) -> None:
    db = SessionLocal()
    pulse = None
    try:
        clip = db.get(Clip, clip_id)
        job = db.get(ProcessingJob, job_id)
        if clip is None or job is None:
            return
        job.status, job.current_stage = "processing", "clip_render"
        job.started_at = datetime.now(timezone.utc)
        job.worker_id = (getattr(self.request, "hostname", None) or "unknown")[:160]
        job.worker_task_id = getattr(self.request, "id", None)
        job.worker_heartbeat_at = datetime.now(timezone.utc)
        job.retry_count = int(getattr(self.request, "retries", 0))
        db.commit()
        pulse = _start_heartbeat(job_id, getattr(self.request, "hostname", None), getattr(self.request, "id", None),
                                 int(getattr(self.request, "retries", 0)))
        project = db.get(Project, clip.project_id)
        transcript = db.get(Transcript, clip.transcript_id) if clip.transcript_id else None
        if not project or not project.source_video_key or not transcript or transcript.status != "completed":
            raise RuntimeError("Clip source video or saved transcript is unavailable for rendering.")
        source = storage.resolve(project.source_video_key)
        _, plan = get_user_plan(db, project.owner_id)
        render_width = min(settings.render_width, int(plan.get("export_width", settings.render_width)))
        render_height = min(settings.render_height, int(plan.get("export_height", settings.render_height)))
        watermark = bool(plan.get("watermark", False))
        version = (db.query(ClipRenderVersion).filter_by(clip_id=clip.id).order_by(ClipRenderVersion.version.desc()).first())
        version_number = (version.version if version else 0) + 1
        base = f"artifacts/{project.id}/clips/{clip.id}-v{version_number}"
        video_key, thumb_key, preview_key = f"{base}.mp4", f"artifacts/{project.id}/thumbnails/{clip.id}-v{version_number}.jpg", f"artifacts/{project.id}/previews/{clip.id}-v{version_number}.mp4"
        metadata = dict(clip.metadata_json or {})
        crop_settings = metadata.get("crop_settings") or {"mode": "auto"}
        if crop_settings.get("mode") == "manual":
            crop = manual_crop_plan(source, float(clip.start_seconds), float(clip.end_seconds),
                                    float(crop_settings.get("center_x", 0.5)), float(crop_settings.get("center_y", 0.5)),
                                    float(crop_settings.get("zoom", 1.0)))
        else:
            crop = track_speakers_and_crops(source, float(clip.start_seconds), float(clip.end_seconds))
        crop_key = f"artifacts/{project.id}/crop/{clip.id}-v{version_number}.json"
        crop_path = storage.resolve(crop_key)
        crop_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        crop_path.write_text(json.dumps(crop, separators=(",", ":")), encoding="utf-8")
        storage.commit(crop_key)
        caption_key = f"artifacts/{project.id}/captions/{clip.id}-v{version_number}.ass"
        caption_path = storage.resolve(caption_key)
        style = metadata.get("caption_style", "creator")
        options = metadata.get("caption_options", {})
        caption_source = [] if metadata.get("hide_captions") else metadata.get("caption_segments") or transcript.segments or []
        generate_ass({"segments": caption_source}, caption_path, float(clip.start_seconds), float(clip.end_seconds), style, options,
                     render_width, render_height)
        storage.commit(caption_key)
        output = storage.resolve(video_key)
        thumbnail = storage.resolve(thumb_key)
        preview = storage.resolve(preview_key)
        result = render_clip(source, output, float(clip.start_seconds), float(clip.end_seconds), crop, caption_path, thumbnail, preview,
                             width=render_width, height=render_height, watermark=watermark)
        storage.commit(video_key)
        storage.commit(thumb_key)
        storage.commit(preview_key)
        render_settings = {"width": render_width, "height": render_height, "fps": settings.render_fps,
                           "bitrate": settings.render_video_bitrate, "watermark": watermark, "thumbnail_key": thumb_key, "preview_key": preview_key,
                           "caption_key": caption_key, "crop_key": crop_key, "start_time": float(clip.start_seconds),
                           "end_time": float(clip.end_seconds), "crop_settings": crop_settings,
                           "caption_style": style, "caption_options": options,
                           "caption_segments": metadata.get("caption_segments"),
                           "title": clip.title, "description": metadata.get("description", ""), "hashtags": metadata.get("hashtags", []),
                           "export_filename": metadata.get("export_filename", ""),
                           "hide_captions": bool(metadata.get("hide_captions", False))}
        db.add(ClipRenderVersion(clip_id=clip.id, version=version_number, output_key=video_key,
                                 render_settings=render_settings, status="completed", validation_data=result))
        metadata.update({"thumbnail_key": thumb_key, "preview_key": preview_key, "caption_key": caption_key,
                         "crop_key": crop_key, "render_validation": result, "duration_seconds": result["duration_seconds"],
                         "active_render_version": version_number, "rendered_start_time": float(clip.start_seconds),
                         "rendered_end_time": float(clip.end_seconds), "watermark": watermark})
        clip.metadata_json = metadata
        clip.output_video_key = video_key
        clip.status = "completed"
        job.status, job.progress, job.current_stage = "completed", 100, "completed"
        job.completed_at = datetime.now(timezone.utc)
        job.worker_heartbeat_at = datetime.now(timezone.utc)
        settle_job(db, job_id, max(1, settings.credits_per_rendered_clip))
        db.commit()
    except Exception as exc:
        db.rollback()
        clip = db.get(Clip, clip_id)
        job = db.get(ProcessingJob, job_id)
        retrying = self.request.retries < self.max_retries
        if clip and not retrying:
            clip.status = "needs_render" if clip.output_video_key else "failed"
        if job:
            job.error_code = "clip_render_failed"
            job.error_message = "Edited clip rendering failed. Retry once the worker or media configuration is available."
            job.retry_count = int(self.request.retries) + (1 if retrying else 0)
            job.worker_heartbeat_at = datetime.now(timezone.utc)
            if retrying:
                job.status, job.current_stage = "queued", "clip_render"
                job.worker_id, job.worker_task_id = None, None
            else:
                job.status, job.failure_class = "failed", "transient"
                job.dead_lettered_at = datetime.now(timezone.utc)
                job.completed_at = datetime.now(timezone.utc)
                refund_job(db, job_id, "Edited clip render failed; reserved credits returned")
        db.commit()
        logger.exception("Edited clip render failed clip_id=%s job_id=%s", clip_id, job_id)
        if retrying:
            raise self.retry(exc=exc, countdown=5 * (self.request.retries + 1))
    finally:
        _stop_heartbeat(pulse)
        db.close()
