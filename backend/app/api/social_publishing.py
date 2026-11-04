from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.billing.credits import InsufficientCredits, add_credits, charge_credits, ensure_free_allocation
from app.billing.plans import get_user_plan
from app.core.database import get_db
from app.core.rate_limit import enforce_rate_limit
from app.core.security import get_current_user
from app.models.models import (AuditLog, Clip, CreditTransaction, OAuthCredential, PlatformMetadata, Project,
                               PublishingAttempt, PublishingLog, PublishingPost, PublishingSchedule,
                               PublishingTarget, SocialAccount, SocialProvider, SystemConfiguration,
                               Transcript, UsageRecord, User)
from app.social.content import generate_platform_content
from app.social.crypto import decrypt_secret, encrypt_secret
from app.social.providers import ProviderError, get_provider
from app.social.providers.youtube import YouTubeProvider
from app.storage.local import storage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/social", tags=["social publishing"])


class DraftCreate(BaseModel):
    clip_id: str = Field(min_length=1, max_length=64)
    account_ids: list[str] = Field(min_length=1, max_length=20)
    idempotency_key: str = Field(min_length=16, max_length=160)
    timezone: str = Field(default="UTC", min_length=1, max_length=80)


class MetadataGeneration(BaseModel):
    idempotency_key: str = Field(min_length=16, max_length=160)
    audience: str = Field(default="", max_length=200)
    language: str = Field(default="English", max_length=40)
    tone: str = Field(default="clear and engaging", max_length=80)
    call_to_action: str = Field(default="", max_length=200)


class MetadataUpdate(BaseModel):
    metadata: dict[str, Any]
    platform_options: dict[str, Any] = Field(default_factory=dict)


class ScheduleRequest(BaseModel):
    scheduled_at: datetime
    timezone: str = Field(default="UTC", min_length=1, max_length=80)


class BulkScheduleItem(BaseModel):
    clip_id: str = Field(min_length=1, max_length=64)
    account_ids: list[str] = Field(min_length=1, max_length=20)
    idempotency_key: str = Field(min_length=16, max_length=160)
    scheduled_at: datetime
    timezone: str = Field(default="UTC", min_length=1, max_length=80)
    metadata_by_account: dict[str, dict[str, Any]]
    options_by_account: dict[str, dict[str, Any]] = Field(default_factory=dict)


class BulkScheduleRequest(BaseModel):
    items: list[BulkScheduleItem] = Field(min_length=1, max_length=50)


def _log(db: Session, user_id: str, action: str, *, post_id: str | None = None,
         target_id: str | None = None, details: dict | None = None) -> None:
    db.add(PublishingLog(user_id=user_id, post_id=post_id, target_id=target_id, action=action, details=details))
    db.add(AuditLog(actor_user_id=user_id, action=f"social.{action}", target_type="publishing_target" if target_id else "publishing_post",
                    target_id=target_id or post_id, details=details))


def _utc_schedule(value: datetime, timezone_name: str) -> datetime:
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(status_code=422, detail="Select a valid IANA timezone.") from exc
    if value.tzinfo is None:
        localized = value.replace(tzinfo=zone, fold=0)
        if localized.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != value:
            raise HTTPException(status_code=422, detail="This local time does not exist because of a daylight-saving transition.")
    else:
        localized = value.astimezone(zone)
    utc_value = localized.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    if utc_value < now + timedelta(minutes=1):
        raise HTTPException(status_code=422, detail="Schedule at least one minute in the future.")
    if utc_value > now + timedelta(days=365):
        raise HTTPException(status_code=422, detail="Posts can be scheduled up to one year ahead.")
    return utc_value


def _owned_post(db: Session, user: User, post_id: str) -> PublishingPost:
    post = db.query(PublishingPost).filter_by(id=post_id, user_id=user.id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Publishing draft not found.")
    return post


def _owned_target(db: Session, user: User, target_id: str) -> tuple[PublishingTarget, PublishingPost]:
    row = db.query(PublishingTarget, PublishingPost).join(PublishingPost, PublishingPost.id == PublishingTarget.post_id).filter(
        PublishingTarget.id == target_id, PublishingPost.user_id == user.id
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Publishing target not found.")
    return row


def _owned_clip(db: Session, user: User, clip_id: str) -> Clip:
    clip = db.query(Clip).join(Project, Project.id == Clip.project_id).filter(
        Clip.id == clip_id, Project.owner_id == user.id
    ).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Generated clip not found.")
    return clip


def _owned_accounts(db: Session, user: User, account_ids: list[str]) -> list[tuple[SocialAccount, SocialProvider]]:
    if len(account_ids) != len(set(account_ids)):
        raise HTTPException(status_code=422, detail="Select each connected account only once.")
    rows = db.query(SocialAccount, SocialProvider).join(SocialProvider, SocialProvider.id == SocialAccount.provider_id).filter(
        SocialAccount.user_id == user.id, SocialAccount.id.in_(set(account_ids)), SocialAccount.status == "connected"
    ).all()
    if len(rows) != len(account_ids):
        raise HTTPException(status_code=404, detail="One or more connected accounts are unavailable.")
    for account, provider_row in rows:
        if not provider_row.publishing_supported or not get_provider(provider_row.slug):
            raise HTTPException(status_code=501, detail=f"Publishing to {provider_row.display_name} is not implemented in this release.")
        if provider_row.slug != "youtube":
            raise HTTPException(status_code=501, detail="This provider does not yet have a publishing adapter.")
    return rows


def _validate_platform_metadata(provider_slug: str, metadata: dict[str, Any], options: dict[str, Any]) -> None:
    if provider_slug == "youtube":
        try:
            YouTubeProvider.validate(metadata, options)
        except ProviderError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    else:
        raise HTTPException(status_code=501, detail="This platform's publishing requirements are not implemented.")


def _validate_clip_file(clip: Clip) -> tuple[Any, Any]:
    if not clip.output_video_key or not storage.exists(clip.output_video_key):
        raise HTTPException(status_code=422, detail="Render and validate this clip before publishing.")
    path = storage.resolve(clip.output_video_key)
    if not path.is_file() or path.suffix.lower() != ".mp4" or path.stat().st_size <= 0:
        raise HTTPException(status_code=422, detail="Publishing requires a non-empty MP4 render.")
    render = (clip.metadata_json or {}).get("render_validation") or {}
    width, height = int(render.get("width", 0)), int(render.get("height", 0))
    duration = float(render.get("duration_seconds", float(clip.end_seconds - clip.start_seconds)))
    if width <= 0 or height <= 0 or not 1 <= duration <= 43200:
        raise HTTPException(status_code=422, detail="Clip resolution or duration is outside the supported YouTube upload range.")
    if path.stat().st_size > 256 * 1024**3:
        raise HTTPException(status_code=422, detail="The clip exceeds YouTube's 256 GB upload limit.")
    return path, render


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    normalized = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return normalized.isoformat()


def _month_start(value: datetime) -> datetime:
    value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _next_month(month_start: datetime) -> datetime:
    return month_start.replace(year=month_start.year + (month_start.month == 12),
                               month=1 if month_start.month == 12 else month_start.month + 1)


def _monthly_count(db: Session, user_id: str, metric: str, month_start: datetime | None = None) -> int:
    month_start = _month_start(month_start or datetime.now(timezone.utc))
    return int(db.query(func.coalesce(func.sum(UsageRecord.quantity), 0)).filter(
        UsageRecord.user_id == user_id, UsageRecord.metric == metric,
        UsageRecord.created_at >= month_start, UsageRecord.created_at < _next_month(month_start)
    ).scalar() or 0)


def _target_reservation_month(target: PublishingTarget, schedule: PublishingSchedule | None) -> datetime | None:
    if target.status in {"scheduled", "paused"}:
        return _month_start(schedule.scheduled_at) if schedule else None
    if target.status in {"queued", "processing", "retry_waiting"}:
        if schedule and schedule.status in {"scheduled", "dispatched"}:
            return _month_start(schedule.scheduled_at)
        return _month_start(target.updated_at)
    return None


def _active_reservations_in_month(db: Session, user_id: str, month_start: datetime) -> int:
    rows = db.query(PublishingTarget, PublishingSchedule).join(
        PublishingPost, PublishingPost.id == PublishingTarget.post_id
    ).outerjoin(PublishingSchedule, PublishingSchedule.target_id == PublishingTarget.id).filter(
        PublishingPost.user_id == user_id,
        PublishingTarget.status.in_(["scheduled", "paused", "queued", "processing", "retry_waiting"])
    ).all()
    return sum(1 for target, schedule in rows if _target_reservation_month(target, schedule) == month_start)


def _serialize_post(db: Session, post: PublishingPost) -> dict[str, Any]:
    targets = db.query(PublishingTarget, SocialAccount, SocialProvider, PublishingSchedule).join(
        SocialProvider, SocialProvider.id == PublishingTarget.provider_id
    ).outerjoin(SocialAccount, SocialAccount.id == PublishingTarget.social_account_id).outerjoin(
        PublishingSchedule, PublishingSchedule.target_id == PublishingTarget.id
    ).filter(PublishingTarget.post_id == post.id).all()
    return {"id": post.id, "clip_id": post.clip_id, "title": post.title, "status": post.status,
            "timezone": post.timezone, "created_at": post.created_at,
            "targets": [{"id": target.id, "account_id": target.social_account_id,
                         "account_name": account.display_name if account else "Disconnected account",
                         "provider": provider.slug, "provider_name": provider.display_name,
                         "status": target.status, "metadata": target.metadata_json or {},
                         "platform_options": target.platform_options or {},
                         "scheduled_at": _iso_utc(schedule.scheduled_at) if schedule else None,
                         "schedule_timezone": schedule.timezone if schedule else None,
                         "external_post_url": target.external_post_url,
                         "last_error_code": target.last_error_code,
                         "last_error_message": target.last_error_message,
                         "published_at": _iso_utc(target.published_at),
                         "analytics": target.analytics_json,
                         } for target, account, provider, schedule in targets]}


def _create_draft(db: Session, user: User, clip: Clip, accounts: list[tuple[SocialAccount, SocialProvider]],
                  idempotency_key: str, timezone_name: str) -> PublishingPost:
    existing = db.query(PublishingPost).filter_by(user_id=user.id, idempotency_key=idempotency_key).first()
    account_ids = {account.id for account, _ in accounts}
    if existing:
        existing_ids = {row[0] for row in db.query(PublishingTarget.social_account_id).filter_by(post_id=existing.id).all()}
        if existing.clip_id != clip.id or existing_ids != account_ids:
            raise HTTPException(status_code=409, detail="This idempotency key was already used for different publishing content.")
        return existing
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(status_code=422, detail="Select a valid IANA timezone.") from exc
    post = PublishingPost(user_id=user.id, clip_id=clip.id, status="draft", title=clip.title,
                          timezone=timezone_name, idempotency_key=idempotency_key)
    db.add(post)
    db.flush()
    for account, provider in accounts:
        target_key = hashlib.sha256(f"{post.id}:{account.id}".encode()).hexdigest()
        db.add(PublishingTarget(post_id=post.id, social_account_id=account.id, provider_id=provider.id,
                                status="draft", metadata_json={}, platform_options={"privacy_status": "private", "made_for_kids": None},
                                idempotency_key=target_key))
    _log(db, user.id, "draft.created", post_id=post.id, details={"clip_id": clip.id, "target_count": len(accounts)})
    db.flush()
    return post


@router.post("/drafts")
def create_draft(payload: DraftCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    clip = _owned_clip(db, user, payload.clip_id)
    _validate_clip_file(clip)
    accounts = _owned_accounts(db, user, payload.account_ids)
    post = _create_draft(db, user, clip, accounts, payload.idempotency_key, payload.timezone)
    db.commit()
    return _serialize_post(db, post)


@router.post("/posts/{post_id}/metadata/generate")
def generate_metadata(post_id: str, payload: MetadataGeneration, request: Request,
                      user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    enforce_rate_limit(request, scope="social-ai-metadata", limit=20)
    post = _owned_post(db, user, post_id)
    if post.status not in {"draft", "scheduled"}:
        raise HTTPException(status_code=409, detail="Metadata cannot be changed after publishing starts.")
    clip = _owned_clip(db, user, post.clip_id or "")
    transcript = db.query(Transcript).filter_by(project_id=clip.project_id, status="completed").order_by(Transcript.created_at.desc()).first()
    transcript_text = str(transcript.text or "") if transcript else ""
    transcript_scope = "available project transcript fallback"
    if transcript and isinstance(transcript.segments, list):
        clip_segments = []
        for segment in transcript.segments:
            if not isinstance(segment, dict):
                continue
            try:
                overlaps = float(segment.get("end", 0)) > float(clip.start_seconds) and float(segment.get("start", 0)) < float(clip.end_seconds)
            except (TypeError, ValueError):
                overlaps = False
            if overlaps and str(segment.get("text", "")).strip():
                clip_segments.append(str(segment["text"]).strip())
        if clip_segments:
            transcript_text = " ".join(clip_segments)
            transcript_scope = "clip-specific timed transcript excerpt"
        else:
            clip_excerpt = str((clip.metadata_json or {}).get("transcript") or "").strip()
            if clip_excerpt:
                transcript_text = clip_excerpt
                transcript_scope = "clip analysis transcript excerpt"
    if not transcript_text.strip():
        raise HTTPException(status_code=422, detail="A completed transcript is required for transcript-grounded metadata generation.")
    _, plan = get_user_plan(db, user.id)
    db.query(User).filter_by(id=user.id).with_for_update().first()
    targets = db.query(PublishingTarget, SocialProvider).join(SocialProvider, SocialProvider.id == PublishingTarget.provider_id).filter(
        PublishingTarget.post_id == post.id
    ).all()
    generated_by_target = {}
    pending = []
    for target, provider_row in targets:
        generation_key = hashlib.sha256(f"{payload.idempotency_key}:{target.id}".encode()).hexdigest()
        existing = db.query(PlatformMetadata).filter_by(idempotency_key=generation_key).first()
        if existing:
            generated_by_target[target.id] = existing.metadata_json
        else:
            pending.append((target, provider_row, generation_key))
    already_used = _monthly_count(db, user.id, "social_ai_metadata")
    if already_used + len(pending) > int(plan.get("monthly_ai_metadata_generations", 0)):
        raise HTTPException(status_code=429, detail="Your plan's monthly AI metadata generation limit has been reached.")
    charge = int(plan.get("social_ai_metadata_credits", 1))
    generated_count = 0
    errors = []
    for target, provider_row, generation_key in pending:
        short_key = hashlib.sha256(f"social-ai:{generation_key}".encode()).hexdigest()
        transaction_key = f"social-ai:{short_key}"
        refunded = db.query(CreditTransaction.id).filter_by(idempotency_key=f"social-ai-refund:{short_key}").first()
        if refunded:
            errors.append({"target_id": target.id, "code": "generation_previously_refunded", "message": "Use a new generation request to retry."})
            continue
        charged = False
        try:
            ensure_free_allocation(db, user.id)
            if charge > 0:
                charge_credits(db, user.id, charge, idempotency_key=transaction_key,
                               transaction_type="social_ai_metadata", description=f"AI metadata generation for {provider_row.slug}")
                db.commit()
                charged = True
            clip_context = (clip.metadata_json or {}) | {"title": clip.title, "transcript": transcript_text,
                "transcript_scope": transcript_scope,
                "duration_seconds": float(clip.end_seconds - clip.start_seconds)}
            metadata, model = generate_platform_content(db=db, platform=provider_row.slug, transcript=transcript_text,
                clip=clip_context, audience=payload.audience, language=payload.language,
                tone=payload.tone, call_to_action=payload.call_to_action)
            version = int(db.query(func.max(PlatformMetadata.version)).filter_by(target_id=target.id).scalar() or 0) + 1
            row = PlatformMetadata(target_id=target.id, version=version, idempotency_key=generation_key,
                                   metadata_json=metadata, generation_model=model, status="generated")
            target.metadata_json = metadata
            target.status = "draft"
            db.add(row)
            db.add(UsageRecord(user_id=user.id, project_id=clip.project_id, metric="social_ai_metadata",
                               quantity=1, unit="generation", period_start=datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0),
                               metadata_json={"target_id": target.id, "platform": provider_row.slug, "model": model}))
            _log(db, user.id, "metadata.generated", post_id=post.id, target_id=target.id,
                 details={"platform": provider_row.slug, "version": version})
            db.commit()
            generated_by_target[target.id] = metadata
            generated_count += 1
        except InsufficientCredits as exc:
            db.rollback()
            raise HTTPException(status_code=402, detail="Not enough credits for AI metadata generation.") from exc
        except Exception as exc:
            db.rollback()
            if charged:
                try:
                    add_credits(db, user.id, charge, "refund", idempotency_key=f"social-ai-refund:{short_key}",
                                reference_id=target.id, description="AI metadata generation failed; credits returned")
                    _log(db, user.id, "metadata.generation_failed", post_id=post.id, target_id=target.id,
                         details={"platform": provider_row.slug, "code": getattr(exc, "code", "metadata_generation_failed")})
                    db.commit()
                except Exception:
                    db.rollback()
                    logger.exception("Failed to refund social metadata generation user_id=%s target_id=%s", user.id, target.id)
            errors.append({"target_id": target.id, "code": getattr(exc, "code", "metadata_generation_failed"),
                           "message": str(exc)[:300] if isinstance(exc, (ValueError, ProviderError)) else "Metadata generation failed; charged credits were refunded when possible."})
    if generated_count == 0 and not generated_by_target and errors:
        raise HTTPException(status_code=502, detail={"message": "No platform metadata could be generated.", "errors": errors})
    return {"post_id": post.id, "metadata_by_target": generated_by_target, "errors": errors,
            "generated_count": generated_count, "charged_credits": generated_count * charge}


@router.patch("/targets/{target_id}/metadata")
def update_target_metadata(target_id: str, payload: MetadataUpdate, user: User = Depends(get_current_user),
                           db: Session = Depends(get_db)) -> dict:
    target, post = _owned_target(db, user, target_id)
    if target.status not in {"draft", "scheduled", "paused"}:
        raise HTTPException(status_code=409, detail="Metadata can no longer be edited in this publishing state.")
    provider = db.get(SocialProvider, target.provider_id)
    metadata = payload.metadata
    options = payload.platform_options or target.platform_options or {}
    _validate_platform_metadata(provider.slug, metadata, options)
    target.metadata_json, target.platform_options = metadata, options
    latest = db.query(PlatformMetadata).filter_by(target_id=target.id).order_by(PlatformMetadata.version.desc()).first()
    version = (latest.version if latest else 0) + 1
    db.add(PlatformMetadata(target_id=target.id, version=version, metadata_json=metadata, status="edited"))
    _log(db, user.id, "metadata.edited", post_id=post.id, target_id=target.id, details={"platform": provider.slug})
    db.commit()
    return {"target_id": target.id, "metadata": target.metadata_json, "platform_options": target.platform_options}


def _check_schedule_limits(db: Session, user: User, targets: list[PublishingTarget], plan: dict[str, Any], *,
                           scheduled_at: datetime | None = None, scheduled: bool = True) -> None:
    db.query(User).filter_by(id=user.id).with_for_update().first()
    if scheduled:
        scheduled_count = db.query(func.count(PublishingTarget.id)).join(PublishingPost, PublishingPost.id == PublishingTarget.post_id).filter(
            PublishingPost.user_id == user.id, PublishingTarget.status.in_(["scheduled", "paused"])
        ).scalar() or 0
        replaces_scheduled = sum(1 for target in targets if target.status in {"scheduled", "paused"})
        if scheduled_count + len(targets) - replaces_scheduled > int(plan.get("max_scheduled_posts", 0)):
            raise HTTPException(status_code=429, detail="Your plan's scheduled-post limit has been reached.")
    month_start = _month_start(scheduled_at or datetime.now(timezone.utc))
    published = _monthly_count(db, user.id, "social_published_target", month_start)
    active = _active_reservations_in_month(db, user.id, month_start)
    replaces_active = 0
    for target in targets:
        schedule = db.query(PublishingSchedule).filter_by(target_id=target.id).first()
        if _target_reservation_month(target, schedule) == month_start:
            replaces_active += 1
    if published + active - replaces_active + len(targets) > int(plan.get("monthly_publishing_volume", 0)):
        raise HTTPException(status_code=429, detail="Your plan's monthly publishing volume has been reached.")


def _schedule_target(db: Session, target: PublishingTarget, scheduled_at: datetime, timezone_name: str) -> None:
    schedule = db.query(PublishingSchedule).filter_by(target_id=target.id).first()
    if schedule is None:
        schedule = PublishingSchedule(target_id=target.id, scheduled_at=scheduled_at, timezone=timezone_name, status="scheduled")
        db.add(schedule)
    else:
        schedule.scheduled_at, schedule.timezone = scheduled_at, timezone_name
        schedule.status, schedule.cancelled_at, schedule.dispatched_at = "scheduled", None, None
    target.status = "scheduled"
    target.next_retry_at = None
    target.lease_expires_at = None


@router.post("/posts/{post_id}/schedule")
def schedule_post(post_id: str, payload: ScheduleRequest, request: Request,
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    enforce_rate_limit(request, scope="social-schedule", limit=20)
    post = _owned_post(db, user, post_id)
    targets = db.query(PublishingTarget).filter_by(post_id=post.id).all()
    if not targets or any(target.status not in {"draft", "scheduled", "paused"} for target in targets):
        raise HTTPException(status_code=409, detail="Only draft or already scheduled targets can be scheduled.")
    scheduled_at = _utc_schedule(payload.scheduled_at, payload.timezone)
    _, plan = get_user_plan(db, user.id)
    _check_schedule_limits(db, user, targets, plan, scheduled_at=scheduled_at)
    for target in targets:
        provider = db.get(SocialProvider, target.provider_id)
        _validate_platform_metadata(provider.slug, target.metadata_json or {}, target.platform_options or {})
        account = db.get(SocialAccount, target.social_account_id) if target.social_account_id else None
        if not account or account.status != "connected":
            raise HTTPException(status_code=409, detail="Reconnect every target account before scheduling.")
        _schedule_target(db, target, scheduled_at, payload.timezone)
        _log(db, user.id, "post.scheduled", post_id=post.id, target_id=target.id,
             details={"scheduled_at_utc": scheduled_at.isoformat(), "timezone": payload.timezone})
    post.status, post.timezone = "scheduled", payload.timezone
    db.commit()
    return _serialize_post(db, post)


@router.post("/posts/{post_id}/publish")
def publish_post_now(post_id: str, request: Request, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)) -> dict:
    enforce_rate_limit(request, scope="social-publish", limit=10)
    post = _owned_post(db, user, post_id)
    targets = db.query(PublishingTarget).filter_by(post_id=post.id).all()
    if not targets or any(target.status not in {"draft", "scheduled", "paused", "failed"} for target in targets):
        raise HTTPException(status_code=409, detail="One or more publishing targets are already processing or complete.")
    _, plan = get_user_plan(db, user.id)
    _check_schedule_limits(db, user, targets, plan, scheduled=False)
    for target in targets:
        provider = db.get(SocialProvider, target.provider_id)
        _validate_platform_metadata(provider.slug, target.metadata_json or {}, target.platform_options or {})
        account = db.get(SocialAccount, target.social_account_id) if target.social_account_id else None
        if not account or account.status != "connected":
            raise HTTPException(status_code=409, detail="Reconnect every target account before publishing.")
    for target in targets:
        schedule = db.query(PublishingSchedule).filter_by(target_id=target.id).first()
        if schedule:
            schedule.status, schedule.cancelled_at = "cancelled", datetime.now(timezone.utc)
        target.status = "queued"
        target.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=2)
        _log(db, user.id, "post.publish_queued", post_id=post.id, target_id=target.id)
    post.status = "queued"
    db.commit()
    from app.workers.tasks import publish_social_target
    for target in targets:
        try:
            publish_social_target.apply_async(args=[target.id])
        except Exception:
            logger.exception("Could not enqueue social publishing target_id=%s", target.id)
            target.status, target.last_error_code = "failed", "queue_unavailable"
            target.last_error_message = "The publishing queue is unavailable. Try again later."
            target.lease_expires_at = None
            db.commit()
    return _serialize_post(db, post)


@router.post("/posts/bulk-schedule")
def bulk_schedule(payload: BulkScheduleRequest, request: Request,
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    enforce_rate_limit(request, scope="social-bulk-schedule", limit=5)
    _, plan = get_user_plan(db, user.id)
    if not bool(plan.get("bulk_scheduling", False)):
        raise HTTPException(status_code=403, detail="Bulk scheduling is not available on your current plan.")
    if len({item.idempotency_key for item in payload.items}) != len(payload.items):
        raise HTTPException(status_code=422, detail="Each bulk item needs a distinct idempotency key.")
    prevalidated = []
    requested_targets = 0
    new_targets = 0
    db.query(User).filter_by(id=user.id).with_for_update().first()
    for item in payload.items:
        clip = _owned_clip(db, user, item.clip_id)
        _validate_clip_file(clip)
        accounts = _owned_accounts(db, user, item.account_ids)
        scheduled_at = _utc_schedule(item.scheduled_at, item.timezone)
        for account, provider in accounts:
            metadata = item.metadata_by_account.get(account.id)
            if metadata is None:
                raise HTTPException(status_code=422, detail="Bulk scheduling needs metadata for every selected account.")
            options = item.options_by_account.get(account.id, {"privacy_status": "private", "made_for_kids": None})
            _validate_platform_metadata(provider.slug, metadata, options)
        existing = db.query(PublishingPost).filter_by(user_id=user.id, idempotency_key=item.idempotency_key).first()
        if existing:
            existing_ids = {row[0] for row in db.query(PublishingTarget.social_account_id).filter_by(post_id=existing.id).all()}
            if existing.clip_id != clip.id or existing_ids != {account.id for account, _ in accounts}:
                raise HTTPException(status_code=409, detail="This bulk idempotency key was already used for different publishing content.")
        else:
            new_targets += len(accounts)
        prevalidated.append((item, clip, accounts, scheduled_at, existing))
        requested_targets += len(accounts)
    scheduled_count = db.query(func.count(PublishingTarget.id)).join(PublishingPost, PublishingPost.id == PublishingTarget.post_id).filter(
        PublishingPost.user_id == user.id, PublishingTarget.status.in_(["scheduled", "paused"])
    ).scalar() or 0
    if scheduled_count + new_targets > int(plan.get("max_scheduled_posts", 0)):
        raise HTTPException(status_code=429, detail="Your plan's scheduled-post limit would be exceeded by this batch.")
    additions_by_month: dict[datetime, int] = {}
    for item, _clip, accounts, scheduled_at, existing in prevalidated:
        if not existing:
            month_start = _month_start(scheduled_at)
            additions_by_month[month_start] = additions_by_month.get(month_start, 0) + len(accounts)
    for month_start, additions in additions_by_month.items():
        published = _monthly_count(db, user.id, "social_published_target", month_start)
        active = _active_reservations_in_month(db, user.id, month_start)
        if published + active + additions > int(plan.get("monthly_publishing_volume", 0)):
            raise HTTPException(status_code=429, detail="Your plan's monthly publishing volume would be exceeded by this batch.")
    posts = []
    try:
        for item, clip, accounts, scheduled_at, existing in prevalidated:
            if existing:
                posts.append(existing)
                continue
            post = _create_draft(db, user, clip, accounts, item.idempotency_key, item.timezone)
            for account, provider in accounts:
                target = db.query(PublishingTarget).filter_by(post_id=post.id, social_account_id=account.id).one()
                target.metadata_json = item.metadata_by_account[account.id]
                target.platform_options = item.options_by_account.get(account.id, {"privacy_status": "private", "made_for_kids": None})
                _schedule_target(db, target, scheduled_at, item.timezone)
                _log(db, user.id, "post.scheduled", post_id=post.id, target_id=target.id,
                     details={"scheduled_at_utc": scheduled_at.isoformat(), "bulk": True})
            post.status, post.timezone = "scheduled", item.timezone
            posts.append(post)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"posts": [_serialize_post(db, post) for post in posts], "scheduled_targets": requested_targets}


@router.patch("/targets/{target_id}/schedule")
def reschedule_target(target_id: str, payload: ScheduleRequest, user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)) -> dict:
    target, post = _owned_target(db, user, target_id)
    if target.status not in {"scheduled", "paused"}:
        raise HTTPException(status_code=409, detail="Only scheduled or paused posts can be rescheduled.")
    scheduled_at = _utc_schedule(payload.scheduled_at, payload.timezone)
    _, plan = get_user_plan(db, user.id)
    _check_schedule_limits(db, user, [target], plan, scheduled_at=scheduled_at)
    _schedule_target(db, target, scheduled_at, payload.timezone)
    _log(db, user.id, "post.rescheduled", post_id=post.id, target_id=target.id,
         details={"scheduled_at_utc": scheduled_at.isoformat(), "timezone": payload.timezone})
    db.commit()
    return _serialize_post(db, post)


@router.post("/targets/{target_id}/cancel")
def cancel_target(target_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    target, post = _owned_target(db, user, target_id)
    if target.status not in {"scheduled", "paused", "queued", "retry_waiting"}:
        raise HTTPException(status_code=409, detail="This post can no longer be cancelled safely.")
    schedule = db.query(PublishingSchedule).filter_by(target_id=target.id).first()
    if schedule:
        schedule.status, schedule.cancelled_at = "cancelled", datetime.now(timezone.utc)
    target.status, target.lease_expires_at = "cancelled", None
    _log(db, user.id, "post.cancelled", post_id=post.id, target_id=target.id)
    db.commit()
    return _serialize_post(db, post)


@router.post("/targets/{target_id}/pause")
def pause_target(target_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    target, post = _owned_target(db, user, target_id)
    schedule = db.query(PublishingSchedule).filter_by(target_id=target.id).first()
    if target.status != "scheduled" or not schedule:
        raise HTTPException(status_code=409, detail="Only future scheduled posts can be paused.")
    target.status, schedule.status = "paused", "paused"
    _log(db, user.id, "post.paused", post_id=post.id, target_id=target.id)
    db.commit()
    return _serialize_post(db, post)


@router.post("/targets/{target_id}/resume")
def resume_target(target_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    target, post = _owned_target(db, user, target_id)
    schedule = db.query(PublishingSchedule).filter_by(target_id=target.id).first()
    if target.status != "paused" or not schedule or schedule.scheduled_at.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc):
        raise HTTPException(status_code=409, detail="This paused schedule cannot be resumed; choose a future time.")
    target.status, schedule.status = "scheduled", "scheduled"
    _log(db, user.id, "post.resumed", post_id=post.id, target_id=target.id)
    db.commit()
    return _serialize_post(db, post)


@router.post("/targets/{target_id}/retry")
def retry_target(target_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    target, post = _owned_target(db, user, target_id)
    if target.status == "reconciliation_required":
        raise HTTPException(status_code=409, detail="The provider may already have published this video. Check the platform before attempting a new upload.")
    if target.status != "failed":
        raise HTTPException(status_code=409, detail="Only a failed publishing target can be retried.")
    account = db.query(SocialAccount).filter_by(id=target.social_account_id, user_id=user.id, status="connected").first()
    if not account:
        raise HTTPException(status_code=409, detail="Reconnect the publishing account before retrying.")
    _, plan = get_user_plan(db, user.id)
    _check_schedule_limits(db, user, [target], plan, scheduled=False)
    target.status, target.next_retry_at = "queued", None
    target.last_error_code, target.last_error_message = None, None
    target.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=2)
    _log(db, user.id, "post.retry_queued", post_id=post.id, target_id=target.id)
    post.status = "queued"
    db.commit()
    from app.workers.tasks import publish_social_target
    try:
        publish_social_target.apply_async(args=[target.id])
    except Exception:
        target.status, target.last_error_code = "failed", "queue_unavailable"
        target.last_error_message, target.lease_expires_at = "The publishing queue is unavailable.", None
        db.commit()
        raise HTTPException(status_code=503, detail="The publishing queue is unavailable.")
    return _serialize_post(db, post)


@router.get("/posts")
def list_publishing_posts(status_filter: str | None = Query(default=None, alias="status"),
                          user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    query = db.query(PublishingPost).filter_by(user_id=user.id)
    if status_filter:
        if status_filter not in {"draft", "scheduled", "queued", "processing", "published", "partial", "failed"}:
            raise HTTPException(status_code=422, detail="Unsupported publishing status filter.")
        query = query.filter_by(status=status_filter)
    posts = query.order_by(PublishingPost.created_at.desc()).limit(200).all()
    return {"posts": [_serialize_post(db, post) for post in posts]}


@router.get("/calendar")
def publishing_calendar(start: datetime, end: datetime, timezone_name: str = Query(default="UTC", alias="timezone"),
                       user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    start_utc, end_utc = _utc_range(start, end, timezone_name)
    rows = db.query(PublishingSchedule, PublishingTarget, PublishingPost, SocialAccount, SocialProvider).join(
        PublishingTarget, PublishingTarget.id == PublishingSchedule.target_id
    ).join(PublishingPost, PublishingPost.id == PublishingTarget.post_id).join(
        SocialProvider, SocialProvider.id == PublishingTarget.provider_id
    ).outerjoin(SocialAccount, SocialAccount.id == PublishingTarget.social_account_id).filter(
        PublishingPost.user_id == user.id, PublishingSchedule.scheduled_at >= start_utc,
        PublishingSchedule.scheduled_at < end_utc, PublishingSchedule.status.in_(["scheduled", "paused", "dispatched"])
    ).order_by(PublishingSchedule.scheduled_at.asc()).all()
    return {"timezone": timezone_name, "items": [{
        "target_id": target.id, "post_id": post.id, "clip_id": post.clip_id, "title": post.title,
        "provider": provider.slug, "provider_name": provider.display_name,
        "account_name": account.display_name if account else "Disconnected account",
        "status": target.status, "schedule_status": schedule.status,
        "scheduled_at_utc": _iso_utc(schedule.scheduled_at), "timezone": schedule.timezone,
        "metadata": target.metadata_json,
    } for schedule, target, post, account, provider in rows]}


def _utc_range(start: datetime, end: datetime, timezone_name: str) -> tuple[datetime, datetime]:
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(status_code=422, detail="Select a valid IANA timezone.") from exc
    start_utc = start.replace(tzinfo=zone) if start.tzinfo is None else start
    end_utc = end.replace(tzinfo=zone) if end.tzinfo is None else end
    start_utc, end_utc = start_utc.astimezone(timezone.utc), end_utc.astimezone(timezone.utc)
    if end_utc <= start_utc or (end_utc - start_utc) > timedelta(days=93):
        raise HTTPException(status_code=422, detail="Choose a calendar range no longer than 93 days.")
    return start_utc, end_utc


@router.get("/history")
def publishing_history(status_filter: str | None = Query(default=None, alias="status"),
                       user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    query = db.query(PublishingPost).filter_by(user_id=user.id)
    if status_filter in {"published", "partial", "failed"}:
        query = query.filter_by(status=status_filter)
    posts = query.order_by(PublishingPost.updated_at.desc()).limit(200).all()
    return {"posts": [_serialize_post(db, post) for post in posts]}


def refresh_account_token(db: Session, account: SocialAccount) -> str:
    provider = db.query(SocialProvider).filter_by(id=account.provider_id).first()
    implementation = get_provider(provider.slug) if provider else None
    credential = db.query(OAuthCredential).filter_by(social_account_id=account.id).first()
    if not implementation or not credential:
        account.status = "reauth_required"
        db.commit()
        raise ProviderError("reauth_required", "Reconnect this social account to continue publishing.")
    now = datetime.now(timezone.utc)
    if credential.expires_at and credential.expires_at.replace(tzinfo=timezone.utc) <= now + timedelta(minutes=2):
        if not credential.refresh_token_encrypted:
            account.status = "reauth_required"
            db.commit()
            raise ProviderError("reauth_required", "Reconnect this account to refresh its access token.")
        try:
            refresh = decrypt_secret(credential.refresh_token_encrypted)
        except HTTPException as exc:
            account.status = "reauth_required"
            db.commit()
            raise ProviderError("reauth_required", "Stored credentials could not be decrypted; reconnect this account.") from exc
        try:
            token = implementation.refresh_token(refresh, settings.google_oauth_client_id, settings.google_oauth_client_secret)
        except ProviderError as exc:
            if exc.code in {"oauth_exchange_failed", "oauth_response_invalid"}:
                account.status = "reauth_required"
                db.commit()
                raise ProviderError("reauth_required", "Reconnect this social account; its refresh permission is no longer valid.") from exc
            raise
        credential.access_token_encrypted = encrypt_secret(str(token["access_token"]))
        if token.get("refresh_token"):
            credential.refresh_token_encrypted = encrypt_secret(str(token["refresh_token"]))
        credential.expires_at = now + timedelta(seconds=max(60, min(86400, int(token.get("expires_in", 3600)))))
        credential.last_refreshed_at = now
        db.commit()
    try:
        return decrypt_secret(credential.access_token_encrypted)
    except HTTPException as exc:
        account.status = "reauth_required"
        db.commit()
        raise ProviderError("reauth_required", "Stored credentials could not be decrypted; reconnect this account.") from exc
