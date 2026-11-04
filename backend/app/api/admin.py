from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from celery import current_app
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.billing.credits import InsufficientCredits, ensure_free_allocation, estimate_job_credits, refund_job, reserve_for_job
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import enforce_rate_limit
from app.core.security import require_admin
from app.models.models import (AuditLog, Clip, CreditTransaction, CreditWallet, ProcessingJob,
                               Project, Subscription, SystemConfiguration, UsageRecord, User, UserSession)
from app.services.job_queue import dispatch_job

router = APIRouter(prefix="/api/admin", tags=["administration"])


class AdminCreditAdjustment(BaseModel):
    amount: int = Field(ge=-1_000_000, le=1_000_000)
    idempotency_key: str = Field(min_length=8, max_length=160)
    description: str = Field(min_length=1, max_length=512)


class UserStatusUpdate(BaseModel):
    is_active: bool


class AIProviderConfig(BaseModel):
    transcription_provider: str
    ai_provider: str
    ai_model: str = ""
    transcription_model: str = ""
    fallback_provider: str | None = None
    fallback_model: str = ""
    timeout_seconds: int = Field(default=120, ge=5, le=600)
    max_retries: int = Field(default=2, ge=0, le=5)


def _audit(db: Session, admin: User, action: str, target_type: str | None = None,
           target_id: str | None = None, details: dict[str, Any] | None = None) -> None:
    db.add(AuditLog(actor_user_id=admin.id, action=action, target_type=target_type,
                    target_id=target_id, details=details))


def _size_bytes(root: Path) -> int:
    if not root.exists():
        return 0
    total = 0
    for path in root.rglob("*"):
        if path.is_file():
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total


@router.get("/health")
def admin_health(admin: User = Depends(require_admin)) -> dict[str, str]:
    return {"status": "ok", "role": "admin", "user_id": admin.id}


@router.get("/overview")
def overview(admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    active_subs = db.query(Subscription).filter(Subscription.status.in_({"active", "trialing"})).all()
    from app.billing.plans import get_plan_catalog
    catalog = get_plan_catalog(db)
    mrr_cents = sum(int(catalog.get(row.plan_key, {}).get("price_cents", 0)) for row in active_subs)
    total_users = db.query(func.count(User.id)).scalar() or 0
    active_users = db.query(func.count(User.id)).filter(User.is_active.is_(True), User.last_login_at >= datetime.now(timezone.utc) - timedelta(days=30)).scalar() or 0
    total_projects = db.query(func.count(Project.id)).scalar() or 0
    total_clips = db.query(func.count(Clip.id)).scalar() or 0
    failed_jobs = db.query(func.count(ProcessingJob.id)).filter(ProcessingJob.status == "failed").scalar() or 0
    usage = db.query(UsageRecord.metric, func.sum(UsageRecord.quantity)).group_by(UsageRecord.metric).all()
    worker_health: Any = {"available": False, "workers": []}
    try:
        pings = current_app.control.inspect(timeout=0.5).ping() or {}
        worker_health = {"available": bool(pings), "workers": sorted(pings)}
    except Exception:
        pass
    return {
        "users_total": total_users, "users_active": active_users,
        "subscriptions_active": len(active_subs), "mrr_estimate_cents": mrr_cents,
        "projects_total": total_projects, "clips_total": total_clips, "jobs_failed": failed_jobs,
        "usage_totals": {str(metric): float(value or 0) for metric, value in usage},
        "storage_bytes_local": _size_bytes(Path(settings.local_storage_path)),
        "worker_health": worker_health,
    }


@router.get("/users")
def list_users(offset: int = 0, limit: int = 50, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    if offset < 0 or not 1 <= limit <= 100:
        raise HTTPException(status_code=422, detail="Use offset >= 0 and a page size between 1 and 100.")
    users = db.query(User).order_by(User.created_at.desc()).offset(offset).limit(limit).all()
    return {"users": [{"id": row.id, "email": row.email, "display_name": row.display_name,
                       "is_active": row.is_active, "is_admin": row.is_admin, "created_at": row.created_at}
                      for row in users]}


@router.patch("/users/{user_id}")
def update_user_status(user_id: str, payload: UserStatusUpdate, request: Request,
                       admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    enforce_rate_limit(request, scope="admin-user-status", limit=30)
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    if user.id == admin.id and not payload.is_active:
        raise HTTPException(status_code=422, detail="Administrators cannot suspend their own account.")
    user.is_active = payload.is_active
    if not user.is_active:
        from datetime import datetime, timezone
        for session in db.query(UserSession).filter_by(user_id=user.id, revoked_at=None).all():
            session.revoked_at = datetime.now(timezone.utc)
    _audit(db, admin, "admin.user.status", "user", user.id, {"is_active": user.is_active})
    db.commit()
    return {"id": user.id, "is_active": user.is_active}


@router.post("/users/{user_id}/credits")
def adjust_user_credits(user_id: str, payload: AdminCreditAdjustment, request: Request,
                        admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, int]:
    enforce_rate_limit(request, scope="admin-credit-adjust", limit=20)
    if payload.amount == 0:
        raise HTTPException(status_code=422, detail="Credit adjustment cannot be zero.")
    existing = db.query(CreditTransaction).filter_by(idempotency_key=f"admin:{payload.idempotency_key}").first()
    if existing:
        wallet = db.get(CreditWallet, existing.wallet_id)
        return {"available_credits": wallet.balance if wallet else existing.balance_after}
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")
    wallet = db.query(CreditWallet).filter_by(user_id=user_id).with_for_update().first()
    if not wallet:
        wallet = CreditWallet(user_id=user_id, balance=0, reserved=0)
        db.add(wallet)
        db.flush()
    new_balance = wallet.balance + payload.amount
    if new_balance < 0:
        raise HTTPException(status_code=409, detail="Adjustment would make the available credit balance negative.")
    wallet.balance = new_balance
    db.add(CreditTransaction(wallet_id=wallet.id, amount=payload.amount, balance_after=new_balance,
                             transaction_type="adjustment", reference_id=user_id,
                             idempotency_key=f"admin:{payload.idempotency_key}", description=payload.description))
    _audit(db, admin, "admin.credits.adjust", "user", user_id, {"amount": payload.amount, "description": payload.description})
    db.commit()
    return {"available_credits": wallet.balance}


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: str, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, str]:
    enforce_rate_limit(request, scope="admin-job-retry", limit=20)
    job = db.get(ProcessingJob, job_id)
    if not job or job.status != "failed" or job.failure_class == "permanent":
        raise HTTPException(status_code=404, detail="Eligible transient failed processing job not found.")
    user_id = job.requested_by_id
    if not user_id:
        raise HTTPException(status_code=409, detail="Processing job no longer has an owning user.")
    reserve = int((job.options or {}).get("reserved_credits", 0))
    if reserve:
        ensure_free_allocation(db, user_id)
        job.attempt += 1
        try:
            reserve_for_job(db, job, user_id, reserve)
        except InsufficientCredits as exc:
            raise HTTPException(status_code=402, detail="The owner does not have enough credits for retry.") from exc
    if job.job_type == "clip_render":
        clip = db.get(Clip, (job.options or {}).get("clip_id"))
        if not clip:
            raise HTTPException(status_code=409, detail="The clip associated with this render job no longer exists.")
        clip.status = "rendering"
    job.status, job.progress, job.error_code, job.error_message = "queued", 0, None, None
    job.failure_class, job.dead_lettered_at, job.retry_count = None, None, 0
    job.current_stage, job.started_at, job.completed_at = ("clip_render" if job.job_type == "clip_render" else "source_validation"), None, None
    _audit(db, admin, "admin.job.retry", "processing_job", job.id)
    db.commit()
    if not dispatch_job(job, db):
        refund_job(db, job.id, "Processing queue unavailable; reserved credits returned")
        db.commit()
    return {"job_id": job.id, "status": job.status}


@router.get("/jobs/stale")
def list_stale_jobs(admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.processing_heartbeat_stale_seconds)
    rows = db.query(ProcessingJob, Project.name).join(Project, Project.id == ProcessingJob.project_id).filter(
        ProcessingJob.status == "processing", ProcessingJob.worker_heartbeat_at < cutoff
    ).order_by(ProcessingJob.worker_heartbeat_at.asc()).limit(100).all()
    return {"jobs": [{"id": job.id, "project_id": job.project_id, "project_name": name,
                       "current_stage": job.current_stage, "worker_id": job.worker_id,
                       "worker_heartbeat_at": job.worker_heartbeat_at, "retry_count": job.retry_count}
                      for job, name in rows]}


@router.post("/jobs/{job_id}/recover")
def recover_stale_job(job_id: str, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, str]:
    enforce_rate_limit(request, scope="admin-job-recover", limit=20)
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.processing_heartbeat_stale_seconds)
    job = db.get(ProcessingJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    updated = db.query(ProcessingJob).filter(
        ProcessingJob.id == job_id, ProcessingJob.status == "processing",
        ProcessingJob.worker_heartbeat_at < cutoff,
        ProcessingJob.retry_count < settings.processing_task_max_retries,
        or_(ProcessingJob.failure_class.is_(None), ProcessingJob.failure_class != "permanent"),
    ).update({
        ProcessingJob.status: "queued",
        ProcessingJob.retry_count: ProcessingJob.retry_count + 1,
        ProcessingJob.worker_heartbeat_at: None,
        ProcessingJob.worker_id: None,
        ProcessingJob.worker_task_id: None,
        ProcessingJob.failure_class: None,
        ProcessingJob.dead_lettered_at: None,
        ProcessingJob.started_at: None,
        ProcessingJob.completed_at: None,
        ProcessingJob.error_code: "worker_heartbeat_timeout",
        ProcessingJob.error_message: "Worker heartbeat expired; job was queued for checkpoint recovery by an administrator.",
    }, synchronize_session=False)
    if updated != 1:
        raise HTTPException(status_code=409, detail="Job is not stale and recoverable, or another administrator already recovered it.")
    db.refresh(job)
    _audit(db, admin, "admin.job.recover", "processing_job", job.id)
    db.commit()
    if not dispatch_job(job, db):
        refund_job(db, job.id, "Processing queue unavailable during recovery; reserved credits returned")
        db.commit()
    return {"job_id": job.id, "status": job.status}


@router.get("/jobs/failed")
def list_failed_jobs(offset: int = 0, limit: int = 50, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    if offset < 0 or not 1 <= limit <= 100:
        raise HTTPException(status_code=422, detail="Use offset >= 0 and a page size between 1 and 100.")
    rows = db.query(ProcessingJob, Project.name, User.email).join(Project, Project.id == ProcessingJob.project_id).outerjoin(
        User, User.id == ProcessingJob.requested_by_id).filter(ProcessingJob.status == "failed").order_by(
        ProcessingJob.completed_at.desc().nullslast()).offset(offset).limit(limit).all()
    return {"jobs": [{"id": job.id, "project_id": job.project_id, "project_name": project_name,
                       "owner_email": email, "current_stage": job.current_stage,
                       "error_code": job.error_code, "error_message": job.error_message,
                       "attempt": job.attempt, "created_at": job.created_at} for job, project_name, email in rows]}


@router.get("/audit")
def recent_audit_logs(limit: int = 100, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    if not 1 <= limit <= 200:
        raise HTTPException(status_code=422, detail="Limit must be between 1 and 200.")
    rows = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit).all()
    return {"events": [{"id": row.id, "actor_user_id": row.actor_user_id, "action": row.action,
                        "target_type": row.target_type, "target_id": row.target_id,
                        "created_at": row.created_at} for row in rows]}


@router.get("/ai/providers")
def get_ai_provider_config(admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.query(SystemConfiguration).filter_by(key="ai_providers").first()
    value = row.value if row else {"transcription_provider": settings.transcription_provider, "ai_provider": settings.ai_provider,
                                  "ai_model": settings.ai_model, "fallback_provider": None, "timeout_seconds": 120, "max_retries": 2}
    return {**value, "credentials": {"openai_configured": bool(settings.openai_api_key),
                                     "gemini_configured": bool(settings.gemini_api_key),
                                     "anthropic_configured": bool(settings.anthropic_api_key)},
            "secrets_are_environment_only": True}


@router.put("/ai/providers")
def update_ai_provider_config(payload: AIProviderConfig, admin: User = Depends(require_admin),
                              db: Session = Depends(get_db)) -> dict[str, Any]:
    if payload.transcription_provider not in {"openai", "faster-whisper"}:
        raise HTTPException(status_code=422, detail="Unsupported transcription provider.")
    if payload.ai_provider not in {"openai", "openai-compatible", "gemini", "claude"}:
        raise HTTPException(status_code=422, detail="Unsupported LLM provider.")
    if payload.fallback_provider and payload.fallback_provider not in {"openai", "openai-compatible", "gemini", "claude"}:
        raise HTTPException(status_code=422, detail="Unsupported fallback provider.")
    if payload.ai_provider == "openai-compatible" and not payload.ai_model.strip():
        raise HTTPException(status_code=422, detail="A model name is required for an OpenAI-compatible endpoint.")
    value = payload.model_dump()
    row = db.query(SystemConfiguration).filter_by(key="ai_providers").first()
    if row:
        row.value = value
    else:
        db.add(SystemConfiguration(key="ai_providers", value=value, description="Active server-side AI provider configuration"))
    _audit(db, admin, "admin.ai.providers.update", "configuration", "ai_providers",
           {"ai_provider": payload.ai_provider, "transcription_provider": payload.transcription_provider})
    db.commit()
    return {**value, "credentials": {"openai_configured": bool(settings.openai_api_key),
                                     "gemini_configured": bool(settings.gemini_api_key),
                                     "anthropic_configured": bool(settings.anthropic_api_key)},
            "secrets_are_environment_only": True}


@router.post("/ai/providers/test")
def test_ai_provider(provider: str, admin: User = Depends(require_admin)) -> dict[str, Any]:
    if provider in {"openai", "openai-compatible"}:
        key, url, headers = settings.openai_api_key, settings.openai_base_url.rstrip("/") + "/models", {}
    elif provider == "gemini":
        key, url, headers = settings.gemini_api_key, "https://generativelanguage.googleapis.com/v1beta/models", {}
        if key:
            url += f"?key={key}"
    elif provider in {"claude", "anthropic"}:
        key, url, headers = settings.anthropic_api_key, "https://api.anthropic.com/v1/models", {"anthropic-version": "2023-06-01"}
    else:
        raise HTTPException(status_code=422, detail="Unsupported provider.")
    if not key:
        return {"provider": provider, "connected": False, "message": "Credentials are not configured in the server environment."}
    if provider in {"openai", "openai-compatible"}:
        headers["Authorization"] = f"Bearer {key}"
    elif provider in {"claude", "anthropic"}:
        headers["x-api-key"] = key
    try:
        response = httpx.get(url, headers=headers, timeout=httpx.Timeout(8, connect=3))
        connected = response.is_success
    except httpx.HTTPError:
        connected = False
    return {"provider": provider, "connected": connected,
            "message": "Provider authentication succeeded." if connected else "Provider connectivity/authentication failed; no secrets were returned."}
