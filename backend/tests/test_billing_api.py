import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.core.config import settings
from app.models.models import CreditReservation, CreditTransaction, CreditWallet, ProcessingJob, Project, Subscription, SystemConfiguration, User


def _signature(secret: str, raw: bytes) -> str:
    timestamp = str(int(time.time()))
    value = hmac.new(secret.encode(), timestamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={value}"


def test_admin_api_requires_admin_role(client: TestClient):
    user = client.post("/api/auth/register", json={"email": "nonadmin-stage4@example.com", "password": "SecureLongPassword!23", "display_name": "User"})
    assert user.status_code == 201, user.text
    assert client.get("/api/admin/overview").status_code == 403


def test_stale_job_recovery_is_atomic_and_admin_only(client: TestClient, monkeypatch):
    response = client.post("/api/auth/register", json={
        "email": "recovery-admin@example.com", "password": "SecureLongPassword!23", "display_name": "Admin"
    })
    assert response.status_code == 201, response.text
    user_id = response.json()["id"]
    client.headers["X-CSRF-Token"] = client.cookies.get("clipforge_csrf")
    with client.app.state.test_session_factory() as db:
        user = db.get(User, user_id)
        user.is_admin = True
        project = Project(owner_id=user_id, name="Recovery project", source_video_key="source/recovery.mp4",
                          source_filename="source.mp4", source_metadata={"duration_seconds": 60})
        db.add(project); db.flush()
        job = ProcessingJob(project_id=project.id, requested_by_id=user_id, status="processing",
                            current_stage="transcription", worker_heartbeat_at=datetime.now(timezone.utc) - timedelta(hours=1),
                            retry_count=0, worker_id="worker-old")
        db.add(job); db.commit()
        job_id = job.id

    from app.api import admin as admin_api
    monkeypatch.setattr(admin_api, "dispatch_job", lambda job, db: True)
    recovered = client.post(f"/api/admin/jobs/{job_id}/recover")
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["status"] == "queued"
    duplicate = client.post(f"/api/admin/jobs/{job_id}/recover")
    assert duplicate.status_code == 409
    with client.app.state.test_session_factory() as db:
        job = db.get(ProcessingJob, job_id)
        assert job.status == "queued" and job.retry_count == 1
        assert job.worker_id is None and job.error_code == "worker_heartbeat_timeout"


def test_stripe_test_webhook_signature_and_invoice_allocation_are_idempotent(client: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_unit_test")
    user = User(email="stripe-webhook@example.com", password_hash="unused")
    with client.app.state.test_session_factory() as db:
        db.add(user); db.flush()
        db.add(CreditWallet(user_id=user.id, balance=0, reserved=0))
        db.add(SystemConfiguration(key="pricing_plans", value={"plans": {
            "free": {"name": "Free", "price_cents": 0, "currency": "usd", "monthly_credits": 10, "max_video_seconds": 300, "max_clips": 2, "max_concurrent_jobs": 1, "export_width": 720, "export_height": 1280, "watermark": True, "priority": "standard", "advanced_captions": False, "stripe_price_id": "", "active": True},
            "creator": {"name": "Creator", "price_cents": 999, "currency": "usd", "monthly_credits": 100, "max_video_seconds": 1800, "max_clips": 10, "max_concurrent_jobs": 2, "export_width": 1080, "export_height": 1920, "watermark": False, "priority": "standard", "advanced_captions": False, "stripe_price_id": "price_test", "active": True},
            "pro": {"name": "Pro", "price_cents": 2000, "currency": "usd", "monthly_credits": 200, "max_video_seconds": 3600, "max_clips": 20, "max_concurrent_jobs": 3, "export_width": 1080, "export_height": 1920, "watermark": False, "priority": "priority", "advanced_captions": True, "stripe_price_id": "price_test_pro", "active": True},
            "business": {"name": "Business", "price_cents": 5000, "currency": "usd", "monthly_credits": 500, "max_video_seconds": 7200, "max_clips": 30, "max_concurrent_jobs": 5, "export_width": 1080, "export_height": 1920, "watermark": False, "priority": "priority", "advanced_captions": True, "stripe_price_id": "price_test_biz", "active": True},
        }}))
        db.add(Subscription(user_id=user.id, provider_subscription_id="sub_test", provider_customer_id="cus_test", plan_key="creator", status="active"))
        db.commit()
        user_id = user.id

    event = {"id": "evt_invoice_1", "type": "invoice.paid", "livemode": False,
             "data": {"object": {"id": "in_test_1", "subscription": "sub_test", "billing_reason": "subscription_cycle",
                                  "amount_paid": 999, "currency": "usd", "period_start": 1700000000, "period_end": 1702592000}}}
    raw = json.dumps(event, separators=(",", ":")).encode()
    headers = {"Stripe-Signature": _signature("whsec_unit_test", raw), "Content-Type": "application/json"}
    first = client.post("/api/billing/webhook", content=raw, headers=headers)
    second = client.post("/api/billing/webhook", content=raw, headers=headers)
    assert first.status_code == second.status_code == 200
    with client.app.state.test_session_factory() as db:
        assert db.query(CreditWallet).filter_by(user_id=user_id).one().balance == 100

    tampered = client.post("/api/billing/webhook", content=raw + b" ", headers=headers)
    assert tampered.status_code == 400
    live_event = {**event, "id": "evt_live", "livemode": True}
    live_raw = json.dumps(live_event, separators=(",", ":")).encode()
    assert client.post("/api/billing/webhook", content=live_raw,
                       headers={"Stripe-Signature": _signature("whsec_unit_test", live_raw)}).status_code == 400


def test_project_deletion_refunds_pending_reservation(client: TestClient):
    response = client.post("/api/auth/register", json={
        "email": "delete-refund@example.com", "password": "SecureLongPassword!23", "display_name": "Owner"
    })
    assert response.status_code == 201, response.text
    user_id = response.json()["id"]
    client.headers["X-CSRF-Token"] = client.cookies.get("clipforge_csrf")
    with client.app.state.test_session_factory() as db:
        wallet = db.query(CreditWallet).filter_by(user_id=user_id).one()
        wallet.balance -= 2
        wallet.reserved += 2
        project = Project(owner_id=user_id, name="Queued work", source_video_key="source/delete.mp4",
                          source_filename="source.mp4", source_metadata={"duration_seconds": 60})
        db.add(project); db.flush()
        job = ProcessingJob(project_id=project.id, requested_by_id=user_id, status="queued", job_type="repurpose")
        db.add(job); db.flush()
        db.add(CreditReservation(job_id=job.id, user_id=user_id, amount=2, consumed=0, status="reserved"))
        db.commit()
        project_id, original_balance = project.id, wallet.balance

    deleted = client.delete(f"/api/projects/{project_id}")
    assert deleted.status_code == 204, deleted.text
    with client.app.state.test_session_factory() as db:
        wallet = db.query(CreditWallet).filter_by(user_id=user_id).one()
        assert wallet.balance == original_balance + 2 and wallet.reserved == 0
        assert db.query(CreditReservation).count() == 0
        assert db.query(CreditTransaction).filter_by(transaction_type="refund").count() == 1
