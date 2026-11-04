import hashlib
import hmac
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.billing.credits import InsufficientCredits, add_credits, refund_job, reserve_for_job, settle_job
from app.billing.plans import DEFAULT_PLANS, validate_plan_catalog
from app.core.database import Base
from app.models.models import CreditTransaction, CreditWallet, ProcessingJob, Project, User
from app.api.billing import _verify_stripe_signature


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as db:
        yield db
    engine.dispose()


def make_job(db):
    user = User(email="credits@example.test", password_hash="unused")
    db.add(user); db.flush()
    project = Project(owner_id=user.id, name="credits", source_metadata={"duration_seconds": 300})
    db.add(project); db.flush()
    job = ProcessingJob(project_id=project.id, requested_by_id=user.id, status="queued", options={})
    db.add(job); db.flush()
    return user, job


def test_credit_reservation_settlement_is_atomic_idempotent_and_refunds_unused(session):
    user, job = make_job(session)
    add_credits(session, user.id, 40, "purchase", idempotency_key="seed:one")
    reservation = reserve_for_job(session, job, user.id, 10)
    session.commit()
    wallet = session.query(CreditWallet).filter_by(user_id=user.id).one()
    assert (wallet.balance, wallet.reserved, reservation.status) == (30, 10, "reserved")

    settle_job(session, job.id, 4)
    session.commit()
    wallet = session.query(CreditWallet).filter_by(user_id=user.id).one()
    assert (wallet.balance, wallet.reserved, reservation.consumed, reservation.status) == (36, 0, 4, "settled")
    settle_job(session, job.id, 10)
    session.commit()
    assert session.query(CreditTransaction).filter_by(transaction_type="consumption").count() == 1
    assert session.query(CreditTransaction).filter_by(transaction_type="refund").count() == 1
    assert session.query(CreditWallet).filter_by(user_id=user.id).one().balance == 36


def test_failed_job_refund_and_explicit_retry_use_distinct_idempotency_keys(session):
    user, job = make_job(session)
    add_credits(session, user.id, 20, "purchase", idempotency_key="seed:two")
    reserve_for_job(session, job, user.id, 12)
    session.commit()
    refund_job(session, job.id, "failure")
    session.commit()
    assert session.query(CreditWallet).filter_by(user_id=user.id).one().balance == 20
    assert session.query(CreditWallet).filter_by(user_id=user.id).one().reserved == 0
    job.attempt = 1
    retried = reserve_for_job(session, job, user.id, 12)
    session.commit()
    assert retried.status == "reserved" and retried.attempt == 1
    assert session.query(CreditWallet).filter_by(user_id=user.id).one().balance == 8
    assert session.query(CreditTransaction).filter(CreditTransaction.transaction_type == "reservation").count() == 2


def test_insufficient_credits_never_create_negative_balance(session):
    user, job = make_job(session)
    add_credits(session, user.id, 3, "purchase", idempotency_key="seed:three")
    session.commit()
    with pytest.raises(InsufficientCredits):
        reserve_for_job(session, job, user.id, 4)
    session.rollback()
    wallet = session.query(CreditWallet).filter_by(user_id=user.id).one()
    assert wallet.balance == 3 and wallet.reserved == 0


def test_credit_grant_idempotency_prevents_duplicate_purchase_allocation(session):
    user, _ = make_job(session)
    add_credits(session, user.id, 50, "purchase", idempotency_key="provider:payment:1")
    add_credits(session, user.id, 50, "purchase", idempotency_key="provider:payment:1")
    session.commit()
    assert session.query(CreditWallet).filter_by(user_id=user.id).one().balance == 50
    assert session.query(CreditTransaction).filter_by(idempotency_key="provider:payment:1").count() == 1


def test_plan_catalog_is_validated_and_prices_are_server_configured():
    assert [plan["key"] for plan in __import__("app.billing.plans", fromlist=["public_plans"]).public_plans(DEFAULT_PLANS)] == ["free", "creator", "pro", "business"]
    broken = {**DEFAULT_PLANS, "creator": {**DEFAULT_PLANS["creator"], "price_cents": -1}}
    with pytest.raises(ValueError):
        validate_plan_catalog(broken)


def test_stripe_signature_rejects_bad_and_stale_signatures():
    secret, raw = "whsec_test_secret", b"{\"id\":\"evt_test\"}"
    timestamp = str(int(time.time()))
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
    assert _verify_stripe_signature(raw, f"t={timestamp},v1={digest}", secret)
    assert not _verify_stripe_signature(raw, f"t={timestamp},v1={'0' * 64}", secret)
    stale = "1"
    old_digest = hmac.new(secret.encode(), stale.encode() + b"." + raw, hashlib.sha256).hexdigest()
    assert not _verify_stripe_signature(raw, f"t={stale},v1={old_digest}", secret)
