from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from app.core.database import SessionLocal
from app.models.models import Clip, ProcessingJob, ProcessingStage, Project, Transcript
from app.processing import pipeline as stages
from app.storage.local import storage
from app.video.ffmpeg import MediaError, probe_media


STAGES = tuple(stages.STAGE_NAMES)


def run_processing_stage(job_id: str, stage_name: str, session_factory: sessionmaker[Session] = SessionLocal) -> dict:
    """Execute exactly one pipeline stage; prerequisites must already be persisted."""
    if stage_name not in STAGES:
        raise ValueError(f"Unknown stage {stage_name!r}. Choose one of: {', '.join(STAGES)}")
    db = session_factory()
    try:
        job = db.get(ProcessingJob, job_id)
        if not job:
            raise ValueError("Processing job was not found.")
        project = db.get(Project, job.project_id)
        if not project:
            raise ValueError("Project was not found.")
        source = stages._validate_source(project)
        duration = float((project.source_metadata or {}).get("duration_seconds") or 0)
        sequence = STAGES.index(stage_name) + 3
        job.status = "processing"
        job.current_stage = stage_name
        job.error_code = job.error_message = None
        db.commit()
        if stage_name == "source_validation":
            stage = stages._get_or_create_stage(db, job, stage_name, sequence)
            stage.status = "completed"
            stage.output_data = {"source_key": project.source_video_key, "duration_seconds": duration}
            stage.completed_at = stages._now()
            stages._save_checkpoint(db, job.id, stage_name, stage.output_data)
            result = stage.output_data
        elif stage_name == "audio_extraction":
            result = stages._audio_stage(db, job, project, source)
        elif stage_name == "transcription":
            checkpoint = stages._checkpoint(db, job.id, "audio_extraction")
            audio_key = checkpoint.artifact_key if checkpoint else None
            if not audio_key or not storage.exists(audio_key):
                raise stages.ProcessingFailure("stage_prerequisite_missing", "Run audio_extraction and retain its audio artifact before transcription.")
            transcript, data = stages._transcript_stage(db, job, project, audio_key, duration)
            result = {"transcript_id": transcript.id, "language": transcript.language, "segment_count": len(data.get("segments", []))}
        elif stage_name == "transcript_analysis":
            transcript = db.query(Transcript).filter_by(project_id=project.id, status="completed").order_by(Transcript.created_at.desc()).first()
            if not transcript:
                raise stages.ProcessingFailure("stage_prerequisite_missing", "A completed transcript is required before analysis.")
            data = {"language": transcript.language, "segments": transcript.segments or [], "text": transcript.text, **(transcript.metadata_json or {})}
            result = stages._analysis_stage(db, job, project, transcript, data, duration)
        elif stage_name == "speaker_tracking":
            result = stages._speaker_stage(db, job, project, source)
        elif stage_name == "caption_generation":
            transcript = db.query(Transcript).filter_by(project_id=project.id, status="completed").order_by(Transcript.created_at.desc()).first()
            crop_checkpoint = stages._checkpoint(db, job.id, "speaker_tracking")
            if not transcript or not crop_checkpoint or not crop_checkpoint.artifact_key or not storage.exists(crop_checkpoint.artifact_key):
                raise stages.ProcessingFailure("stage_prerequisite_missing", "A saved transcript and speaker-tracking result are required before caption generation.")
            transcript_data = {"language": transcript.language, "segments": transcript.segments or [], "text": transcript.text}
            crops = stages._read_json(crop_checkpoint.artifact_key)
            result = stages._caption_stage(db, job, project, transcript_data, crops)
        elif stage_name == "rendering":
            crop_checkpoint = stages._checkpoint(db, job.id, "speaker_tracking")
            caption_checkpoint = stages._checkpoint(db, job.id, "caption_generation")
            if not crop_checkpoint or not crop_checkpoint.artifact_key or not caption_checkpoint or not caption_checkpoint.artifact_key:
                raise stages.ProcessingFailure("stage_prerequisite_missing", "Saved crop and caption artifacts are required before rendering.")
            crops = stages._read_json(crop_checkpoint.artifact_key)
            captions = stages._read_json(caption_checkpoint.artifact_key)
            result = stages._render_stage(db, job, project, source, crops, captions)
        else:
            clips = db.query(Clip).filter_by(project_id=project.id).all()
            if not clips:
                raise stages.ProcessingFailure("clips_missing", "No clips exist for output validation.")
            validations = []
            for clip in clips:
                if not clip.output_video_key or not storage.exists(clip.output_video_key):
                    raise stages.ProcessingFailure("render_output_missing", f"Rendered output for clip {clip.id} is missing.")
                info = probe_media(storage.resolve(clip.output_video_key))
                if info["width"] >= info["height"] or info["video_codec"] != "h264":
                    raise stages.ProcessingFailure("render_validation_failed", f"Clip {clip.id} is not a vertical H.264 output.")
                validations.append({"clip_id": clip.id, "width": info["width"], "height": info["height"], "duration": info["duration_seconds"]})
            stage = stages._get_or_create_stage(db, job, stage_name, sequence)
            stage.status = "completed"
            stage.output_data = {"clips": validations}
            stage.completed_at = stages._now()
            stages._save_checkpoint(db, job.id, stage_name, stage.output_data)
            job.status = "completed"
            job.current_stage = "completed"
            job.progress = 100
            job.completed_at = stages._now()
            project.status = "completed"
            db.commit()
            result = stage.output_data
        if stage_name != "output_validation":
            job.status = "processing"
            job.current_stage = stage_name
            job.progress = max(job.progress, stages.STAGE_PROGRESS.get(stage_name, 5))
            db.commit()
        return result
    except Exception as exc:
        db.rollback()
        job = db.get(ProcessingJob, job_id)
        if job:
            stage = stages._get_or_create_stage(db, job, stage_name, STAGES.index(stage_name) + 3)
            stage.status = "failed"
            stage.error_message = str(exc)[:1000]
            stage.completed_at = stages._now()
            job.status = "failed"
            job.current_stage = stage_name
            job.error_code = getattr(exc, "code", "stage_execution_failed")
            job.error_message = str(exc)[:1000]
            job.completed_at = stages._now()
            project = db.get(Project, job.project_id)
            if project:
                project.status = "failed"
            db.commit()
        raise
    finally:
        db.close()
