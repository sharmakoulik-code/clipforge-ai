from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.billing.credits import InsufficientCredits, ensure_free_allocation, estimate_job_credits, refund_job, reserve_for_job
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import enforce_rate_limit
from app.core.security import get_current_user
from app.models.models import Clip, ProcessingJob, Project, User
from app.schemas.api import ProcessingJobRead
from app.services.job_queue import dispatch_job

router = APIRouter(prefix="/api/jobs", tags=["processing jobs"])


def _owned_job(db: Session, user: User, job_id: str) -> ProcessingJob:
    job = db.query(ProcessingJob).join(Project, Project.id == ProcessingJob.project_id).filter(
        ProcessingJob.id == job_id,
        Project.owner_id == user.id,
    ).with_for_update().first()
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


@router.get("/{job_id}", response_model=ProcessingJobRead)
def get_job(job_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ProcessingJob:
    return _owned_job(db, user, job_id)


@router.post("/{job_id}/retry", response_model=ProcessingJobRead, status_code=status.HTTP_202_ACCEPTED)
def retry_job(job_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ProcessingJob:
    enforce_rate_limit(request, scope="job-retry", limit=5)
    job = _owned_job(db, user, job_id)
    if job.status != "failed" or job.failure_class == "permanent":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only transient failed jobs can be retried")
    job.status = "queued"
    job.progress = 0
    job.attempt += 1
    job.current_stage = "clip_render" if job.job_type == "clip_render" else "source_validation"
    job.error_code = None
    job.error_message = None
    job.failure_class = None
    job.dead_lettered_at = None
    job.retry_count = 0
    job.completed_at = None
    job.started_at = None
    project = db.get(Project, job.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    ensure_free_allocation(db, user.id)
    reserve_amount = (max(1, settings.credits_per_rendered_clip) if job.job_type == "clip_render" else
                      int((job.options or {}).get("reserved_credits") or estimate_job_credits(
                          float((project.source_metadata or {}).get("duration_seconds", 0)), int((job.options or {}).get("num_clips", 1)))))
    try:
        reserve_for_job(db, job, user.id, reserve_amount)
    except InsufficientCredits as exc:
        raise HTTPException(status_code=402, detail=f"Retrying this job is estimated at {reserve_amount} credits. Add credits to retry.") from exc
    if job.job_type == "clip_render":
        clip = db.get(Clip, (job.options or {}).get("clip_id"))
        if not clip:
            raise HTTPException(status_code=409, detail="The clip associated with this render job no longer exists.")
        clip.status = "rendering"
    db.commit()
    db.refresh(job)
    if not dispatch_job(job, db):
        refund_job(db, job.id, "Processing queue unavailable; reserved credits returned")
        db.commit()
        db.refresh(job)
    return job
