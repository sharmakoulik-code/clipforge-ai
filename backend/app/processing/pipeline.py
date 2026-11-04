from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.models import Clip, ClipRenderVersion, ProcessingCheckpoint, ProcessingJob, ProcessingStage, Project, Transcript, UsageRecord
from app.storage.local import storage
from app.video.analysis import AnalysisError, analyze_transcript, resolve_analysis_model
from app.video.captions import generate_ass
from app.video.ffmpeg import MediaError, extract_audio
from app.video.render import render_clip
from app.video.transcription import TranscriptionError, file_sha256, transcribe
from app.video.vision import track_speakers_and_crops

logger = logging.getLogger(__name__)

STAGE_NAMES = ["source_validation", "audio_extraction", "transcription", "transcript_analysis", "speaker_tracking", "caption_generation", "rendering", "output_validation"]
STAGE_PROGRESS = {"source_validation": 5, "audio_extraction": 15, "transcription": 35, "transcript_analysis": 50, "speaker_tracking": 60, "caption_generation": 68, "rendering": 70, "output_validation": 98}


class ProcessingFailure(RuntimeError):
    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _artifact_key(project_id: str, name: str) -> str:
    return f"artifacts/{project_id}/{name}"


def _write_json(key: str, value: Any) -> None:
    path = storage.resolve(key)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    storage.commit(key)


def _read_json(key: str) -> Any:
    return json.loads(storage.resolve(key).read_text(encoding="utf-8"))


def _get_or_create_stage(db: Session, job: ProcessingJob, name: str, sequence: int) -> ProcessingStage:
    stage = db.query(ProcessingStage).filter_by(job_id=job.id, name=name).first()
    if stage is None:
        stage = ProcessingStage(job_id=job.id, name=name, sequence=sequence, status="pending")
        db.add(stage)
        db.flush()
    return stage


def _checkpoint(db: Session, job_id: str, stage: str) -> ProcessingCheckpoint | None:
    return db.query(ProcessingCheckpoint).filter_by(job_id=job_id, checkpoint_key=stage).first()


def _save_checkpoint(db: Session, job_id: str, stage: str, data: dict[str, Any], artifact_key: str | None = None) -> None:
    checkpoint = _checkpoint(db, job_id, stage)
    if checkpoint is None:
        checkpoint = ProcessingCheckpoint(job_id=job_id, stage_name=stage, checkpoint_key=stage)
        db.add(checkpoint)
    checkpoint.stage_name = stage
    checkpoint.data = data
    checkpoint.artifact_key = artifact_key
    checkpoint.updated_at = _now()


def _stage_start(db: Session, job: ProcessingJob, name: str, sequence: int) -> ProcessingStage:
    stage = _get_or_create_stage(db, job, name, sequence)
    stage.status = "processing"
    stage.attempt += 1
    stage.started_at = _now()
    stage.error_message = None
    job.status = "processing"
    job.current_stage = name
    job.progress = max(job.progress, STAGE_PROGRESS.get(name, 5))
    job.error_code = None
    job.error_message = None
    db.commit()
    return stage


def _stage_done(db: Session, job: ProcessingJob, stage: ProcessingStage, output: dict[str, Any], artifact: str | None = None) -> None:
    stage.status = "completed"
    stage.output_data = output
    stage.completed_at = _now()
    _save_checkpoint(db, job.id, stage.name, output, artifact)
    job.progress = max(job.progress, STAGE_PROGRESS.get(stage.name, job.progress))
    db.commit()


def _stage_cached(db: Session, job: ProcessingJob, name: str) -> dict[str, Any] | None:
    stage = db.query(ProcessingStage).filter_by(job_id=job.id, name=name, status="completed").first()
    if stage is None:
        return None
    checkpoint = _checkpoint(db, job.id, name)
    if checkpoint and checkpoint.artifact_key and not storage.exists(checkpoint.artifact_key):
        stage.status = "pending"
        stage.output_data = None
        db.commit()
        return None
    return (checkpoint.data if checkpoint and checkpoint.data else stage.output_data) or {}


def _validate_source(project: Project) -> Path:
    if not project.source_video_key or not storage.exists(project.source_video_key):
        raise ProcessingFailure("source_missing", "The uploaded source video could not be found in storage.")
    path = storage.resolve(project.source_video_key)
    if not path.is_file():
        raise ProcessingFailure("source_missing", "The uploaded source video could not be read from storage.")
    metadata = project.source_metadata or {}
    duration = float(metadata.get("duration_seconds") or 0)
    if duration <= 0:
        raise ProcessingFailure("source_duration_invalid", "The source video has no valid duration metadata.")
    return path


def _record_usage(db: Session, job: ProcessingJob, project: Project, metric: str, quantity: float, unit: str, metadata: dict[str, Any]) -> None:
    db.add(UsageRecord(user_id=project.owner_id, project_id=project.id, metric=metric, quantity=quantity, unit=unit, metadata_json=metadata))


def _audio_stage(db: Session, job: ProcessingJob, project: Project, source: Path) -> dict[str, Any]:
    cached = _stage_cached(db, job, "audio_extraction")
    if cached:
        return cached
    stage = _stage_start(db, job, "audio_extraction", 4)
    audio_key = _artifact_key(project.id, "audio.wav")
    path = storage.resolve(audio_key)
    metadata = extract_audio(source, path)
    if not metadata.get("has_audio"):
        raise ProcessingFailure("missing_audio", "No usable audio stream was found. Add an audio track or upload a different source.")
    storage.commit(audio_key)
    metadata.pop("path", None)
    metadata["artifact_key"] = audio_key
    _stage_done(db, job, stage, metadata, audio_key)
    return metadata


def _transcript_stage(db: Session, job: ProcessingJob, project: Project, audio_key: str, duration: float) -> tuple[Transcript, dict[str, Any]]:
    stage_cached = _stage_cached(db, job, "transcription")
    audio_path = storage.resolve(audio_key)
    audio_hash = file_sha256(audio_path)
    provider = str((job.options or {}).get("transcription_provider") or settings.transcription_provider)
    model = str((job.options or {}).get("transcription_model") or "")
    if stage_cached and stage_cached.get("transcript_id"):
        transcript_row = db.get(Transcript, stage_cached["transcript_id"])
        if transcript_row and transcript_row.status == "completed" and transcript_row.segments:
            return transcript_row, {"language": transcript_row.language, "segments": transcript_row.segments, "text": transcript_row.text, **(transcript_row.metadata_json or {})}
    # Reuse a completed transcript with the same source audio and provider configuration.
    cached_row = db.query(Transcript).filter_by(project_id=project.id, status="completed", provider=provider).order_by(Transcript.created_at.desc()).first()
    expected_model = model or ("whisper-1" if provider == "openai" else settings.whisper_model)
    if cached_row and (cached_row.metadata_json or {}).get("audio_sha256") == audio_hash and (cached_row.metadata_json or {}).get("model") == expected_model:
        transcript_data = {"language": cached_row.language, "segments": cached_row.segments, "text": cached_row.text, **(cached_row.metadata_json or {})}
        stage = _stage_start(db, job, "transcription", 5)
        _stage_done(db, job, stage, {"transcript_id": cached_row.id, "cached": True, "audio_sha256": audio_hash})
        return cached_row, transcript_data
    stage = _stage_start(db, job, "transcription", 5)
    try:
        transcript_data = transcribe(audio_path, duration, provider, model or None)
    except TranscriptionError as exc:
        raise ProcessingFailure(exc.code, str(exc), exc.retryable) from exc
    metadata = {"audio_sha256": audio_hash, "model": transcript_data.get("model"), "cached": False}
    transcript_key = _artifact_key(project.id, f"transcript-{audio_hash[:16]}.json")
    _write_json(transcript_key, {**transcript_data, "metadata": metadata})
    row = Transcript(project_id=project.id, provider=provider, language=transcript_data.get("language"), text=transcript_data.get("text"),
                     segments=transcript_data["segments"], metadata_json={**metadata, "artifact_key": transcript_key}, status="completed")
    db.add(row)
    _record_usage(db, job, project, "transcription_audio_seconds", duration, "seconds", {"provider": provider, "model": transcript_data.get("model")})
    db.flush()
    _stage_done(db, job, stage, {"transcript_id": row.id, "cached": False, "audio_sha256": audio_hash}, transcript_key)
    return row, transcript_data


def _analysis_stage(db: Session, job: ProcessingJob, project: Project, transcript_row: Transcript, transcript: dict[str, Any], duration: float) -> dict[str, Any]:
    options = job.options or {}
    min_duration = max(5, min(180, int(options.get("min_clip_seconds", settings.processing_min_clip_seconds))))
    max_duration = max(min_duration, min(180, int(options.get("max_clip_seconds", settings.processing_max_clip_seconds))))
    count = max(1, min(settings.processing_max_clips, int(options.get("num_clips", 3))))
    provider = str(options.get("ai_provider") or settings.ai_provider).lower()
    configured_model = options.get("ai_model")
    if not configured_model and provider == settings.ai_provider.lower():
        configured_model = settings.ai_model
    model = resolve_analysis_model(provider, str(configured_model or ""))
    signature_data = {"transcript_id": transcript_row.id, "provider": provider, "model": model,
                      "minimum": min_duration, "maximum": max_duration, "count": count}
    analysis_hash = hashlib.sha256(json.dumps(signature_data, sort_keys=True).encode()).hexdigest()
    cached = _stage_cached(db, job, "transcript_analysis")
    result = None
    used_cache = False
    if cached and cached.get("analysis_hash") == analysis_hash and cached.get("artifact_key") and storage.exists(cached["artifact_key"]):
        result = _read_json(cached["artifact_key"])
        used_cache = True
    if result is None:
        candidates = db.query(ProcessingCheckpoint).join(ProcessingJob, ProcessingJob.id == ProcessingCheckpoint.job_id).filter(
            ProcessingJob.project_id == project.id, ProcessingCheckpoint.stage_name == "transcript_analysis"
        ).order_by(ProcessingCheckpoint.created_at.desc()).all()
        for checkpoint in candidates:
            if (checkpoint.data or {}).get("analysis_hash") == analysis_hash and checkpoint.artifact_key and storage.exists(checkpoint.artifact_key):
                result = _read_json(checkpoint.artifact_key)
                used_cache = True
                break
    stage = _stage_start(db, job, "transcript_analysis", 6)
    if result is None:
        try:
            result = analyze_transcript(transcript, duration, min_duration, max_duration, count, provider, model or None)
        except AnalysisError as exc:
            raise ProcessingFailure(exc.code, str(exc), exc.retryable) from exc
    analysis_key = _artifact_key(project.id, f"analysis-{transcript_row.id}-{analysis_hash[:12]}.json")
    if not storage.exists(analysis_key):
        _write_json(analysis_key, result)
    clip_ids = []
    for clip_data in result["clips"]:
        existing = db.query(Clip).filter_by(project_id=project.id, start_seconds=clip_data["start_time"], end_seconds=clip_data["end_time"]).first()
        if existing is not None and (existing.metadata_json or {}).get("analysis_hash") == analysis_hash:
            clip_ids.append(existing.id)
            continue
        metadata = {**clip_data, "duration_seconds": clip_data["end_time"] - clip_data["start_time"],
                    "analysis_provider": provider, "analysis_model": model, "analysis_hash": analysis_hash}
        if existing:
            existing.title = clip_data["title"]
            existing.score = clip_data["viral_score"]
            existing.status = "suggested"
            existing.transcript_id = transcript_row.id
            existing.output_video_key = None
            existing.metadata_json = metadata
            clip_ids.append(existing.id)
        else:
            clip = Clip(project_id=project.id, transcript_id=transcript_row.id, title=clip_data["title"],
                        start_seconds=clip_data["start_time"], end_seconds=clip_data["end_time"], score=clip_data["viral_score"],
                        status="suggested", metadata_json=metadata)
            db.add(clip)
            db.flush()
            clip_ids.append(clip.id)
    if not used_cache:
        _record_usage(db, job, project, "llm_analysis_requests", int(result.get("provider_requests", 1)), "request", {"provider": provider, "model": model})
    _stage_done(db, job, stage, {"artifact_key": analysis_key, "clip_ids": clip_ids, "count": len(result["clips"]), "analysis_hash": analysis_hash}, analysis_key)
    return result


def _speaker_stage(db: Session, job: ProcessingJob, project: Project, source: Path) -> dict[str, dict[str, Any]]:
    cached = _stage_cached(db, job, "speaker_tracking")
    if cached and cached.get("artifact_key") and storage.exists(cached["artifact_key"]):
        return _read_json(cached["artifact_key"])
    stage = _stage_start(db, job, "speaker_tracking", 7)
    rows = db.query(Clip).filter_by(project_id=project.id).order_by(Clip.start_seconds).all()
    if not rows:
        raise ProcessingFailure("stage_prerequisite_missing", "Transcript analysis must create clip candidates before speaker tracking.")
    result: dict[str, dict[str, Any]] = {}
    for clip in rows:
        data = track_speakers_and_crops(source, float(clip.start_seconds), float(clip.end_seconds))
        result[clip.id] = data
    key = _artifact_key(project.id, "speaker-tracking.json")
    _write_json(key, result)
    for clip in rows:
        clip_data = dict(clip.metadata_json or {})
        clip_data["speaker_tracking"] = {"method": result[clip.id]["method"], "active_speaker_id": result[clip.id].get("active_speaker_id"), "confidence": result[clip.id].get("active_speaker_confidence", 0)}
        clip.metadata_json = clip_data
    _stage_done(db, job, stage, {"artifact_key": key, "clip_count": len(result)}, key)
    return result


def _caption_stage(db: Session, job: ProcessingJob, project: Project, transcript: dict[str, Any], crops: dict[str, dict[str, Any]]) -> dict[str, str]:
    cached = _stage_cached(db, job, "caption_generation")
    if cached and cached.get("artifact_key") and storage.exists(cached["artifact_key"]):
        return _read_json(cached["artifact_key"])
    stage = _stage_start(db, job, "caption_generation", 8)
    result = {}
    rows = db.query(Clip).filter_by(project_id=project.id).order_by(Clip.start_seconds).all()
    style = str((job.options or {}).get("caption_style", "creator"))
    caption_options = (job.options or {}).get("caption_options") or {}
    for clip in rows:
        key = _artifact_key(project.id, f"captions/{clip.id}.ass")
        path = storage.resolve(key)
        generate_ass(transcript, path, float(clip.start_seconds), float(clip.end_seconds), style, caption_options,
                     settings.render_width, settings.render_height)
        storage.commit(key)
        result[clip.id] = key
        metadata = dict(clip.metadata_json or {})
        metadata["caption_style"] = style
        metadata["caption_options"] = caption_options
        metadata["caption_key"] = key
        clip.metadata_json = metadata
    index_key = _artifact_key(project.id, "captions/index.json")
    _write_json(index_key, result)
    _stage_done(db, job, stage, {"artifact_key": index_key, "clip_count": len(result)}, index_key)
    return result


def _render_stage(db: Session, job: ProcessingJob, project: Project, source: Path, crops: dict[str, dict[str, Any]], captions: dict[str, str]) -> dict[str, Any]:
    cached = _stage_cached(db, job, "rendering")
    if cached and cached.get("clip_ids"):
        completed = db.query(Clip).filter(Clip.id.in_(cached["clip_ids"]), Clip.output_video_key.is_not(None)).count()
        if completed == len(cached["clip_ids"]):
            return cached
    stage = _stage_start(db, job, "rendering", 9)
    clips = db.query(Clip).filter_by(project_id=project.id).order_by(Clip.start_seconds).all()
    total_clips = max(1, len(clips))
    for index, clip in enumerate(clips):
        job.current_stage = f"rendering_clip_{index + 1}_of_{len(clips)}"
        job.progress = max(job.progress, 70 + int(27 * index / total_clips))
        db.commit()
        video_key = _artifact_key(project.id, f"clips/{clip.id}.mp4")
        thumb_key = _artifact_key(project.id, f"thumbnails/{clip.id}.jpg")
        preview_key = _artifact_key(project.id, f"previews/{clip.id}.mp4")
        output_path = storage.resolve(video_key)
        thumb_path = storage.resolve(thumb_key)
        preview_path = storage.resolve(preview_key)
        validation = (clip.metadata_json or {}).get("render_validation")
        if clip.output_video_key == video_key and validation and storage.exists(video_key) and storage.exists(thumb_key) and storage.exists(preview_key):
            job.progress = max(job.progress, 70 + int(27 * (index + 1) / total_clips))
            db.commit()
            continue
        caption_path = storage.resolve(captions[clip.id])
        result = render_clip(source, output_path, float(clip.start_seconds), float(clip.end_seconds), crops[clip.id], caption_path, thumb_path, preview_path)
        storage.commit(video_key)
        storage.commit(thumb_key)
        storage.commit(preview_key)
        metadata = dict(clip.metadata_json or {})
        metadata.update({"thumbnail_key": thumb_key, "preview_key": preview_key, "render_validation": result, "duration_seconds": result["duration_seconds"]})
        clip.metadata_json = metadata
        clip.output_video_key = video_key
        clip.status = "completed"
        render = db.query(ClipRenderVersion).filter_by(clip_id=clip.id, version=1).first()
        if render is None:
            render = ClipRenderVersion(clip_id=clip.id, version=1, output_key=video_key)
            db.add(render)
        render.output_key = video_key
        render.status = "completed"
        render.validation_data = result
        render.render_settings = {"width": settings.render_width, "height": settings.render_height, "fps": settings.render_fps, "bitrate": settings.render_video_bitrate}
        job.progress = max(job.progress, 70 + int(27 * (index + 1) / total_clips))
        db.commit()
    clip_ids = [clip.id for clip in clips]
    _stage_done(db, job, stage, {"clip_ids": clip_ids, "validated_count": len(clip_ids)})
    return {"clip_ids": clip_ids, "validated_count": len(clip_ids)}


def run_processing_job(job_id: str, session_factory: sessionmaker[Session] = SessionLocal) -> None:
    db = session_factory()
    active_stage = "source_validation"
    try:
        job = db.get(ProcessingJob, job_id)
        if job is None:
            logger.warning("Processing job disappeared job_id=%s", job_id)
            return
        project = db.get(Project, job.project_id)
        if project is None:
            raise ProcessingFailure("project_missing", "The project no longer exists.")
        job.status = "processing"
        job.started_at = job.started_at or _now()
        job.error_code = job.error_message = None
        project.status = "processing"
        db.commit()

        active_stage = "source_validation"
        source = _validate_source(project)
        source_stage = _get_or_create_stage(db, job, active_stage, 3)
        if source_stage.status != "completed":
            source_stage.status = "completed"
            source_stage.output_data = {"source_key": project.source_video_key, "duration_seconds": (project.source_metadata or {}).get("duration_seconds")}
            source_stage.completed_at = _now()
            _save_checkpoint(db, job.id, active_stage, source_stage.output_data)
            job.progress = max(job.progress, STAGE_PROGRESS[active_stage])
            db.commit()

        duration = float((project.source_metadata or {}).get("duration_seconds") or 0)
        active_stage = "audio_extraction"
        audio = _audio_stage(db, job, project, source)
        active_stage = "transcription"
        transcript_row, transcript = _transcript_stage(db, job, project, audio["artifact_key"], duration)
        active_stage = "transcript_analysis"
        analysis = _analysis_stage(db, job, project, transcript_row, transcript, duration)
        active_stage = "speaker_tracking"
        crops = _speaker_stage(db, job, project, source)
        active_stage = "caption_generation"
        captions = _caption_stage(db, job, project, transcript, crops)
        active_stage = "rendering"
        render_result = _render_stage(db, job, project, source, crops, captions)
        active_stage = "output_validation"
        validation_stage = _get_or_create_stage(db, job, active_stage, 10)
        validation_stage.status = "completed"
        validation_stage.output_data = {"validated_clip_count": render_result["validated_count"]}
        validation_stage.completed_at = _now()
        _save_checkpoint(db, job.id, active_stage, validation_stage.output_data)
        job.status = "completed"
        job.current_stage = "completed"
        job.progress = 100
        job.completed_at = _now()
        project.status = "completed"
        db.commit()
        logger.info("Video processing completed job_id=%s project_id=%s clips=%s", job.id, project.id, len(analysis.get("clips", [])))
    except ProcessingFailure as exc:
        db.rollback()
        job = db.get(ProcessingJob, job_id)
        if job:
            project = db.get(Project, job.project_id)
            stage = _get_or_create_stage(db, job, active_stage, STAGE_NAMES.index(active_stage) + 3 if active_stage in STAGE_NAMES else 99)
            stage.status = "failed"
            stage.error_message = str(exc)[:1000]
            stage.completed_at = _now()
            job.status = "failed"
            job.current_stage = active_stage
            job.error_code = exc.code
            job.error_message = str(exc)
            job.completed_at = _now()
            if project:
                project.status = "failed"
            db.commit()
        if exc.retryable:
            raise
    except Exception as exc:
        db.rollback()
        logger.exception("Unexpected processing failure job_id=%s stage=%s", job_id, active_stage)
        job = db.get(ProcessingJob, job_id)
        if job:
            project = db.get(Project, job.project_id)
            stage = _get_or_create_stage(db, job, active_stage, STAGE_NAMES.index(active_stage) + 3 if active_stage in STAGE_NAMES else 99)
            stage.status = "failed"
            stage.error_message = (str(exc)[:1000] if isinstance(exc, MediaError) else f"Unexpected processing error: {str(exc)[:900]}")
            job.status = "failed"
            job.current_stage = active_stage
            job.error_code = getattr(exc, "code", "processing_failed")
            job.error_message = (str(exc)[:1000] if isinstance(exc, (MediaError, TranscriptionError, AnalysisError)) else
                                "Processing stopped due to an unexpected worker error. Completed intermediate stages are retained for retry.")
            job.completed_at = _now()
            if project:
                project.status = "failed"
            db.commit()
        if not isinstance(exc, MediaError):
            raise
    finally:
        db.close()
