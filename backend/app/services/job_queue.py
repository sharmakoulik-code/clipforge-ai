import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.models import ProcessingJob
from app.workers.tasks import prepare_uploaded_project

logger = logging.getLogger(__name__)


def enqueue_uploaded_project(job_id: str) -> bool:
    try:
        prepare_uploaded_project.apply_async(args=[job_id])
        return True
    except Exception:
        # Persist a visible failure instead of leaving the job queued forever; users can retry later.
        logger.exception("Could not enqueue processing job job_id=%s", job_id)
        return False


def dispatch_job(job: ProcessingJob, db: Session) -> bool:
    if enqueue_uploaded_project(job.id):
        return True
    job.status = "failed"
    job.current_stage = "worker_queue"
    job.error_code = "queue_unavailable"
    job.error_message = "The processing queue is unavailable. Retry this job when the worker is online."
    job.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(job)
    return False
