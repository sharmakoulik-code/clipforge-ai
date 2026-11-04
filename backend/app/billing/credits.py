from __future__ import annotations

import math
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import CreditReservation, CreditTransaction, CreditWallet, ProcessingJob, Project, Clip
from app.billing.plans import get_user_plan


def estimate_job_credits(duration_seconds: float, num_clips: int) -> int:
    audio_credits = math.ceil(max(0, duration_seconds) / 60 * settings.credits_per_source_minute)
    return max(1, audio_credits + max(0, num_clips) * settings.credits_per_rendered_clip)


def ensure_wallet(db: Session, user_id: str) -> CreditWallet:
    wallet = db.query(CreditWallet).filter_by(user_id=user_id).with_for_update().first()
    if wallet:
        return wallet
    wallet = CreditWallet(user_id=user_id, balance=0, reserved=0)
    db.add(wallet)
    db.flush()
    return wallet


def add_credits(db: Session, user_id: str, amount: int, transaction_type: str, *, idempotency_key: str,
                reference_id: str | None = None, description: str = "") -> CreditTransaction:
    if amount <= 0:
        raise ValueError("Credit grants must be positive.")
    existing = db.query(CreditTransaction).filter_by(idempotency_key=idempotency_key).first()
    if existing:
        return existing
    wallet = ensure_wallet(db, user_id)
    wallet.balance += amount
    transaction = CreditTransaction(wallet_id=wallet.id, amount=amount, balance_after=wallet.balance,
                                    transaction_type=transaction_type, reference_id=reference_id,
                                    idempotency_key=idempotency_key, description=description[:512])
    db.add(transaction)
    db.flush()
    return transaction


def ensure_free_allocation(db: Session, user_id: str) -> None:
    plan_key, plan = get_user_plan(db, user_id)
    if plan_key != "free":
        return
    amount = int(plan.get("monthly_credits", settings.free_monthly_credits))
    month_key = datetime.now(timezone.utc).strftime("%Y-%m")
    key = f"free:monthly:{user_id}:{month_key}"
    if amount > 0 and not db.query(CreditTransaction.id).filter_by(idempotency_key=key).first():
        add_credits(db, user_id, amount, "subscription_allocation", idempotency_key=key,
                    reference_id=month_key, description=f"Free-plan monthly credit allocation for {month_key}")


def reserve_for_job(db: Session, job: ProcessingJob, user_id: str, amount: int) -> CreditReservation:
    existing = db.query(CreditReservation).filter_by(job_id=job.id).with_for_update().first()
    if existing and existing.status in {"reserved", "settled"}:
        return existing
    if amount <= 0:
        raise ValueError("Reservation amount must be positive.")
    wallet = ensure_wallet(db, user_id)
    changed = db.execute(update(CreditWallet).where(CreditWallet.id == wallet.id, CreditWallet.balance >= amount)
                          .values(balance=CreditWallet.balance - amount, reserved=CreditWallet.reserved + amount))
    if changed.rowcount != 1:
        raise InsufficientCredits()
    db.refresh(wallet)
    attempt = int(job.attempt or 0)
    if existing:
        existing.amount, existing.consumed, existing.status = amount, 0, "reserved"
        existing.attempt, existing.settled_at = attempt, None
        reservation = existing
    else:
        reservation = CreditReservation(job_id=job.id, user_id=user_id, amount=amount, consumed=0, attempt=attempt, status="reserved")
        db.add(reservation)
    db.add(CreditTransaction(wallet_id=wallet.id, amount=-amount, balance_after=wallet.balance,
                             transaction_type="reservation", reference_id=f"{job.id}:{attempt}",
                             idempotency_key=f"job:{job.id}:attempt:{attempt}:reservation", description=f"Credits reserved for processing job {job.id}"))
    db.flush()
    return reservation


def settle_job(db: Session, job_id: str, actual_amount: int) -> CreditReservation | None:
    reservation = db.query(CreditReservation).filter_by(job_id=job_id).with_for_update().first()
    if not reservation or reservation.status != "reserved":
        return reservation
    actual = max(0, min(int(actual_amount), reservation.amount))
    wallet = db.query(CreditWallet).filter_by(user_id=reservation.user_id).with_for_update().first()
    if wallet is None:
        raise ValueError("Credit wallet is missing for reservation.")
    unused = reservation.amount - actual
    wallet.reserved = max(0, wallet.reserved - reservation.amount)
    wallet.balance += unused
    reservation.consumed = actual
    reservation.status = "settled"
    reservation.settled_at = datetime.now(timezone.utc)
    attempt = reservation.attempt
    if actual:
        db.add(CreditTransaction(wallet_id=wallet.id, amount=-actual, balance_after=wallet.balance,
                                 transaction_type="consumption", reference_id=f"{job_id}:{attempt}",
                                 idempotency_key=f"job:{job_id}:attempt:{attempt}:consumption", description=f"Processing credits consumed for job {job_id}"))
    if unused:
        db.add(CreditTransaction(wallet_id=wallet.id, amount=unused, balance_after=wallet.balance,
                                 transaction_type="refund", reference_id=f"{job_id}:{attempt}",
                                 idempotency_key=f"job:{job_id}:attempt:{attempt}:settlement-refund", description=f"Unused processing reservation returned for job {job_id}"))
    db.flush()
    return reservation


def refund_job(db: Session, job_id: str, reason: str = "Processing failed") -> CreditReservation | None:
    reservation = db.query(CreditReservation).filter_by(job_id=job_id).with_for_update().first()
    if not reservation or reservation.status != "reserved":
        return reservation
    wallet = db.query(CreditWallet).filter_by(user_id=reservation.user_id).with_for_update().first()
    if wallet is None:
        raise ValueError("Credit wallet is missing for reservation.")
    wallet.balance += reservation.amount
    wallet.reserved = max(0, wallet.reserved - reservation.amount)
    reservation.status = "refunded"
    reservation.settled_at = datetime.now(timezone.utc)
    db.add(CreditTransaction(wallet_id=wallet.id, amount=reservation.amount, balance_after=wallet.balance,
                             transaction_type="refund", reference_id=f"{job_id}:{reservation.attempt}",
                             idempotency_key=f"job:{job_id}:attempt:{reservation.attempt}:failure-refund", description=reason[:512]))
    db.flush()
    return reservation


def actual_job_credits(db: Session, job: ProcessingJob) -> int:
    project = db.get(Project, job.project_id)
    duration = float((project.source_metadata or {}).get("duration_seconds", 0)) if project else 0
    clips = db.query(Clip).filter_by(project_id=job.project_id).count()
    return estimate_job_credits(duration, clips)


class InsufficientCredits(ValueError):
    pass
