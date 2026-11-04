import logging
from datetime import datetime, timezone

from app.core.database import SessionLocal
from app.models.models import ProcessingJob, ProcessingStage, Project
from app.storage.local import storage
from app.workers.celery_app import celery

logger = logging.getLogger(__name__)


@celery.task(name="app.workers.tasks.prepare_uploaded_project", bind=True, max_retries=3)
def prepare_uploaded_project(self, job_id: str) -> None:
    """Validate persisted upload state and leave the job ready for Stage 3 workers."""
    db = SessionLocal()
    try:
        job = db.get(ProcessingJob, job_id)
        if job is None:
            logger.warning("Queued processing job disappeared job_id=%s", job_id)
            return
        project = db.get(Project, job.project_id)
        if project is None or not project.source_video_key or not storage.exists(project.source_video_key):
            job.status = "failed"
            job.error_code = "source_missing"
            job.error_message = "The uploaded source could not be found in storage."
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
            return
        validation = db.query(ProcessingStage).filter_by(job_id=job.id, name="source_validation").first()
        if validation is None:
            validation = ProcessingStage(
                job_id=job.id,
                name="source_validation",
                sequence=3,
                status="completed",
                output_data={"source_key_present": True},
                completed_at=datetime.now(timezone.utc),
            )
            db.add(validation)
        job.status = "queued"
        job.current_stage = "awaiting_stage_3"
        job.progress = max(job.progress, 5)
        job.error_code = None
        job.error_message = None
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to prepare processing job job_id=%s", job_id)
        raise self.retry(countdown=5)
    finally:
        db.close()
