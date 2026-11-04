import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.models import Clip, ProcessingJob
from app.workers.tasks import prepare_uploaded_project

logger = logging.getLogger(__name__)


def enqueue_uploaded_project(job_id: str, job_type: str = "repurpose", options: dict | None = None) -> bool:
    try:
        if job_type == "clip_render":
            clip_id = (options or {}).get("clip_id")
            if not clip_id:
                logger.error("Clip render job is missing its clip_id job_id=%s", job_id)
                return False
            from app.workers.tasks import render_edited_clip
            render_edited_clip.apply_async(args=[clip_id, job_id])
        else:
            prepare_uploaded_project.apply_async(args=[job_id])
        return True
    except Exception:
        # Persist a visible failure instead of leaving the job queued forever; users can retry later.
        logger.exception("Could not enqueue processing job job_id=%s", job_id)
        return False


def dispatch_job(job: ProcessingJob, db: Session) -> bool:
    if enqueue_uploaded_project(job.id, job.job_type, job.options):
        return True
    job.status = "failed"
    job.current_stage = "worker_queue"
    job.error_code = "queue_unavailable"
    job.error_message = "The processing queue is unavailable. Retry this job when the worker is online."
    job.completed_at = datetime.now(timezone.utc)
    if job.job_type == "clip_render":
        clip = db.get(Clip, (job.options or {}).get("clip_id"))
        if clip:
            clip.status = "needs_render" if clip.output_video_key else "failed"
    db.commit()
    db.refresh(job)
    return False
