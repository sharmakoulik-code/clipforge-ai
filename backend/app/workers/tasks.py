import json
import logging
import random
from threading import Event, Thread
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func

from app.billing.credits import actual_job_credits, refund_job, settle_job
from app.billing.plans import get_user_plan
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.models import (Clip, ClipRenderVersion, ProcessingJob, Project, PublishingPost,
                               PublishingTarget, Transcript)
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


def _refresh_publishing_post_status(db, post_id: str) -> None:
    post = db.get(PublishingPost, post_id)
    if not post:
        return
    statuses = [row[0] for row in db.query(PublishingTarget.status).filter_by(post_id=post_id).all()]
    if not statuses:
        post.status = "draft"
    elif all(value == "published" for value in statuses):
        post.status = "published"
    elif any(value in {"processing", "queued", "retry_waiting"} for value in statuses):
        post.status = "processing" if any(value == "processing" for value in statuses) else "queued"
    elif any(value == "scheduled" for value in statuses):
        post.status = "scheduled"
    elif any(value == "published" for value in statuses):
        post.status = "partial"
    elif all(value in {"cancelled"} for value in statuses):
        post.status = "cancelled"
    else:
        post.status = "failed"


@celery.task(name="app.workers.tasks.publish_social_target", bind=True, max_retries=0)
def publish_social_target(self, target_id: str) -> None:
    """Publish one account-target at a time; uncertain provider outcomes are never blindly retried."""
    from sqlalchemy import update
    from app.models.models import (AuditLog, Clip, OAuthCredential, ProcessingJob, Project, PublishingAttempt,
                                   PublishingLog, PublishingPost, PublishingSchedule, PublishingTarget,
                                   SocialAccount, SocialProvider, UsageRecord)
    from app.social.crypto import decrypt_secret
    from app.social.providers import ProviderError, get_provider
    from app.storage.local import storage
    from app.api.social_publishing import refresh_account_token

    db = SessionLocal()
    publish_started = False
    try:
        now = datetime.now(timezone.utc)
        claimed = db.execute(update(PublishingTarget).where(
            PublishingTarget.id == target_id, PublishingTarget.status == "queued"
        ).values(status="processing", lease_expires_at=now + timedelta(hours=3), last_error_code=None, last_error_message=None))
        if claimed.rowcount != 1:
            db.rollback()
            return
        target = db.get(PublishingTarget, target_id)
        post = db.get(PublishingPost, target.post_id)
        account = db.get(SocialAccount, target.social_account_id) if target.social_account_id else None
        provider_row = db.get(SocialProvider, target.provider_id)
        if not post or not account or account.user_id != post.user_id or account.status != "connected" or not provider_row:
            target.status, target.last_error_code = "failed", "account_unavailable"
            target.last_error_message = "Reconnect the selected social account before publishing."
            target.lease_expires_at = None
            _refresh_publishing_post_status(db, target.post_id)
            db.commit()
            return
        provider = get_provider(provider_row.slug)
        if not provider or not provider_row.publishing_supported:
            target.status, target.last_error_code = "failed", "provider_unavailable"
            target.last_error_message = "This publishing provider is not implemented or enabled."
            target.lease_expires_at = None
            _refresh_publishing_post_status(db, post.id)
            db.commit()
            return
        clip = db.query(Clip).join(Project, Project.id == Clip.project_id).filter(
            Clip.id == post.clip_id, Project.owner_id == post.user_id
        ).first() if post.clip_id else None
        if not clip or not clip.output_video_key:
            target.status, target.last_error_code = "failed", "clip_unavailable"
            target.last_error_message = "The selected rendered clip is no longer available."
            target.lease_expires_at = None
            _refresh_publishing_post_status(db, post.id)
            db.commit()
            return
        attempt_number = int(db.query(func.max(PublishingAttempt.attempt_number)).filter_by(target_id=target.id).scalar() or 0) + 1
        attempt = PublishingAttempt(target_id=target.id, attempt_number=attempt_number, status="processing")
        db.add(attempt)
        target.lease_expires_at = now + timedelta(hours=3)
        db.commit()

        from app.api.social_publishing import _validate_clip_file
        video_path, _ = _validate_clip_file(clip)
        access_token = refresh_account_token(db, account)
        publish_started = True
        result = provider.publish(video_path, target.metadata_json or {}, target.platform_options or {},
                                  access_token, request_key=target.idempotency_key)
        completed = datetime.now(timezone.utc)
        attempt.status, attempt.completed_at = "published", completed
        attempt.provider_request_id = result.provider_request_id
        target.status, target.external_post_id, target.external_post_url = "published", result.external_post_id, result.external_post_url
        target.published_at, target.lease_expires_at = completed, None
        target.next_retry_at, target.retry_count = None, 0
        schedule = db.query(PublishingSchedule).filter_by(target_id=target.id).first()
        if schedule:
            schedule.status, schedule.dispatched_at = "published", completed
        post_owner = post.user_id
        month_start = completed.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        db.add(UsageRecord(user_id=post_owner, project_id=clip.project_id, metric="social_published_target",
                           quantity=1, unit="published target", period_start=month_start,
                           metadata_json={"target_id": target.id, "platform": provider_row.slug}))
        db.add(PublishingLog(user_id=post_owner, post_id=post.id, target_id=target.id,
                             action="post.published", details={"provider": provider_row.slug, "external_post_id": result.external_post_id}))
        db.add(AuditLog(actor_user_id=post_owner, action="social.post.published", target_type="publishing_target",
                        target_id=target.id, details={"provider": provider_row.slug}))
        _refresh_publishing_post_status(db, post.id)
        db.commit()
    except ProviderError as exc:
        db.rollback()
        target = db.get(PublishingTarget, target_id)
        if not target:
            return
        attempt = db.query(PublishingAttempt).filter_by(target_id=target.id).order_by(PublishingAttempt.attempt_number.desc()).first()
        now = datetime.now(timezone.utc)
        if exc.code == "token_invalid" or exc.code == "reauth_required":
            account = db.get(SocialAccount, target.social_account_id) if target.social_account_id else None
            if account:
                account.status = "reauth_required"
        if exc.uncertain:
            target.status = "reconciliation_required"
            target.last_error_code = "publish_result_uncertain"
            target.last_error_message = "The platform may have accepted this upload. Check the connected account before retrying."
            if attempt:
                attempt.status, attempt.error_code = "uncertain", exc.code
                attempt.error_message = "Provider response did not confirm whether the post was created."
                attempt.completed_at = now
        elif exc.retryable and target.retry_count < 5:
            target.retry_count += 1
            target.status = "retry_waiting"
            target.next_retry_at = now + timedelta(seconds=min(3600, 30 * (2 ** (target.retry_count - 1))))
            target.last_error_code, target.last_error_message = exc.code, str(exc)[:900]
            if attempt:
                attempt.status, attempt.error_code, attempt.error_message = "retry_waiting", exc.code, str(exc)[:900]
                attempt.completed_at = now
        else:
            target.status = "failed"
            target.last_error_code, target.last_error_message = exc.code, str(exc)[:900]
            if attempt:
                attempt.status, attempt.error_code, attempt.error_message = "failed", exc.code, str(exc)[:900]
                attempt.completed_at = now
        target.lease_expires_at = None
        schedule = db.query(PublishingSchedule).filter_by(target_id=target.id).first()
        if schedule and target.status in {"failed", "reconciliation_required"}:
            schedule.status = target.status
        if target.post_id:
            _refresh_publishing_post_status(db, target.post_id)
        db.add(PublishingLog(user_id=db.get(PublishingPost, target.post_id).user_id, post_id=target.post_id,
                             target_id=target.id, action=f"post.{target.status}",
                             details={"code": exc.code, "retryable": exc.retryable, "uncertain": exc.uncertain}))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Unexpected social publishing worker failure target_id=%s", target_id)
        target = db.get(PublishingTarget, target_id)
        if target:
            now = datetime.now(timezone.utc)
            attempt = db.query(PublishingAttempt).filter_by(target_id=target.id).order_by(PublishingAttempt.attempt_number.desc()).first()
            if publish_started:
                target.status, target.last_error_code = "reconciliation_required", "publish_result_uncertain"
                target.last_error_message = "The platform may have accepted this upload. Check it before retrying."
                if attempt:
                    attempt.status, attempt.error_code = "uncertain", "unexpected_after_publish_start"
                    attempt.error_message = "Publishing outcome could not be confirmed."
                    attempt.completed_at = now
            else:
                target.status, target.last_error_code = "failed", "publishing_worker_error"
                target.last_error_message = "Publishing failed before the provider upload was started."
                if attempt:
                    attempt.status, attempt.error_code = "failed", "publishing_worker_error"
                    attempt.error_message = target.last_error_message
                    attempt.completed_at = now
            target.lease_expires_at = None
            _refresh_publishing_post_status(db, target.post_id)
            db.commit()
    finally:
        db.close()


@celery.task(name="app.workers.tasks.dispatch_due_social_publications")
def dispatch_due_social_publications() -> dict[str, int]:
    from app.models.models import PublishingAttempt, PublishingLog, PublishingPost, PublishingSchedule, PublishingTarget
    from app.social.providers import ProviderError
    db = SessionLocal()
    now = datetime.now(timezone.utc)
    dispatch_ids: list[str] = []
    recovered = 0
    try:
        stuck = db.query(PublishingTarget).filter(
            PublishingTarget.status == "processing", PublishingTarget.lease_expires_at < now
        ).with_for_update(skip_locked=True).limit(200).all()
        for target in stuck:
            target.status = "reconciliation_required"
            target.last_error_code = "worker_lost_result_uncertain"
            target.last_error_message = "A worker stopped before confirming the provider result. Check the platform before retrying."
            target.lease_expires_at = None
            attempt = db.query(PublishingAttempt).filter_by(target_id=target.id).order_by(PublishingAttempt.attempt_number.desc()).first()
            if attempt and attempt.status == "processing":
                attempt.status, attempt.error_code = "uncertain", "worker_lost_result_uncertain"
                attempt.error_message = target.last_error_message
                attempt.completed_at = now
            schedule = db.query(PublishingSchedule).filter_by(target_id=target.id).first()
            if schedule:
                schedule.status = "reconciliation_required"
            _refresh_publishing_post_status(db, target.post_id)
            db.add(PublishingLog(user_id=db.query(PublishingPost.user_id).filter_by(id=target.post_id).scalar(),
                                 post_id=target.post_id, target_id=target.id, action="post.reconciliation_required",
                                 details={"code": target.last_error_code}))
            recovered += 1

        due_schedules = db.query(PublishingSchedule, PublishingTarget).join(
            PublishingTarget, PublishingTarget.id == PublishingSchedule.target_id
        ).filter(PublishingSchedule.status == "scheduled", PublishingTarget.status == "scheduled",
                 PublishingSchedule.scheduled_at <= now).with_for_update(skip_locked=True).limit(200).all()
        for schedule, target in due_schedules:
            target.status, target.lease_expires_at = "queued", now + timedelta(minutes=2)
            schedule.status, schedule.dispatched_at = "dispatched", now
            _refresh_publishing_post_status(db, target.post_id)
            dispatch_ids.append(target.id)

        retry_targets = db.query(PublishingTarget).filter(
            PublishingTarget.status == "retry_waiting", PublishingTarget.next_retry_at <= now
        ).with_for_update(skip_locked=True).limit(200).all()
        for target in retry_targets:
            target.status, target.next_retry_at = "queued", None
            target.lease_expires_at = now + timedelta(minutes=2)
            dispatch_ids.append(target.id)

        orphaned = db.query(PublishingTarget).filter(
            PublishingTarget.status == "queued", PublishingTarget.lease_expires_at < now
        ).with_for_update(skip_locked=True).limit(200).all()
        for target in orphaned:
            target.lease_expires_at = now + timedelta(minutes=2)
            dispatch_ids.append(target.id)
        db.commit()
        enqueued = 0
        for target_id in dispatch_ids:
            try:
                publish_social_target.apply_async(args=[target_id])
                enqueued += 1
            except Exception:
                logger.exception("Could not enqueue due social publishing target_id=%s", target_id)
                target = db.get(PublishingTarget, target_id)
                if target and target.status == "queued":
                    target.status, target.last_error_code = "failed", "queue_unavailable"
                    target.last_error_message, target.lease_expires_at = "The publishing queue is unavailable.", None
                    _refresh_publishing_post_status(db, target.post_id)
                    db.commit()
        return {"dispatched": enqueued, "reconciliation_required": recovered}
    finally:
        db.close()
