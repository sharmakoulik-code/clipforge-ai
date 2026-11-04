from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.rate_limit import enforce_rate_limit
from app.core.security import get_current_user
from app.models.models import ProcessingJob, Project, User
from app.schemas.api import ProcessingJobRead
from app.services.job_queue import dispatch_job

router = APIRouter(prefix="/api/jobs", tags=["processing jobs"])


def _owned_job(db: Session, user: User, job_id: str) -> ProcessingJob:
    job = db.query(ProcessingJob).join(Project, Project.id == ProcessingJob.project_id).filter(
        ProcessingJob.id == job_id,
        Project.owner_id == user.id,
    ).first()
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
    if job.status != "failed":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only failed jobs can be retried")
    job.status = "queued"
    job.progress = 0
    job.attempt += 1
    job.current_stage = "source_validation"
    job.error_code = None
    job.error_message = None
    job.completed_at = None
    job.started_at = None
    db.commit()
    db.refresh(job)
    dispatch_job(job, db)
    return job
