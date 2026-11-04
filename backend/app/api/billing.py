from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.billing.credits import add_credits, ensure_free_allocation, estimate_job_credits
from app.billing.plans import get_plan_catalog, public_plans
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import enforce_rate_limit
from app.core.security import get_current_user, require_admin
from app.models.models import AuditLog, CreditTransaction, CreditWallet, Payment, Subscription, User, WebhookEvent

router = APIRouter(tags=["billing and credits"])


class CheckoutRequest(BaseModel):
    plan_key: str = Field(min_length=1, max_length=80)


def _stripe_test_key() -> str:
    key = settings.stripe_secret_key
    if not key.startswith("sk_test_"):
        raise HTTPException(status_code=503, detail="Stripe test mode is not configured. Set a Stripe test secret key; live payments are disabled by this application.")
    return key


def _stripe_request(path: str, data: dict[str, str], idempotency_key: str | None = None) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {_stripe_test_key()}"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    try:
        response = httpx.post(f"https://api.stripe.com/v1/{path}", data=data, headers=headers, timeout=httpx.Timeout(20, connect=5))
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Invalid Stripe response")
        return payload
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail="Stripe could not complete the request. Check test-mode product and price configuration.") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Stripe is temporarily unavailable.") from exc


def _verify_stripe_signature(raw: bytes, signature: str, secret: str, tolerance_seconds: int = 300) -> bool:
    values: dict[str, list[str]] = {}
    for item in signature.split(","):
        key, separator, value = item.strip().partition("=")
        if separator:
            values.setdefault(key, []).append(value)
    timestamps, signatures = values.get("t", []), values.get("v1", [])
    if not timestamps or not signatures:
        return False
    try:
        timestamp = int(timestamps[0])
    except ValueError:
        return False
    if abs(int(time.time()) - timestamp) > tolerance_seconds:
        return False
    signed = str(timestamp).encode() + b"." + raw
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, candidate) for candidate in signatures)


def _stripe_get(path: str) -> dict[str, Any]:
    try:
        response = httpx.get(f"https://api.stripe.com/v1/{path}", headers={"Authorization": f"Bearer {_stripe_test_key()}"}, timeout=httpx.Timeout(20, connect=5))
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("Invalid Stripe response")
        return result
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail="Stripe could not retrieve the subscription. Check test-mode configuration.") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Stripe is temporarily unavailable.") from exc


@router.get("/api/billing/plans")
def plans(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return public_plans(get_plan_catalog(db))


@router.get("/api/billing/subscription")
def current_subscription(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    subscription = db.query(Subscription).filter_by(user_id=user.id).order_by(Subscription.created_at.desc()).first()
    catalog = get_plan_catalog(db)
    plan_key = subscription.plan_key if subscription else "free"
    plan = catalog.get(plan_key, catalog["free"])
    return {"plan_key": plan_key, "status": subscription.status if subscription else "active",
            "current_period_start": subscription.current_period_start if subscription else None,
            "current_period_end": subscription.current_period_end if subscription else None,
            "cancel_at_period_end": subscription.cancel_at_period_end if subscription else False,
            "plan": {key: value for key, value in plan.items() if key != "stripe_price_id"}}


@router.get("/api/credits/balance")
def credit_balance(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, int]:
    ensure_free_allocation(db, user.id)
    wallet = db.query(CreditWallet).filter_by(user_id=user.id).first()
    db.commit()
    return {"available_credits": wallet.balance if wallet else 0, "reserved_credits": wallet.reserved if wallet else 0}


@router.get("/api/credits/estimate")
def estimate_credits(duration_seconds: float, num_clips: int = 3, user: User = Depends(get_current_user)) -> dict[str, int]:
    if duration_seconds <= 0 or duration_seconds > 24 * 60 * 60 or not 1 <= num_clips <= settings.processing_max_clips:
        raise HTTPException(status_code=422, detail="Provide a positive duration up to 24 hours and a valid clip count.")
    return {"estimated_credits": estimate_job_credits(duration_seconds, num_clips)}


@router.get("/api/credits/transactions")
def credit_transactions(offset: int = 0, limit: int = 50, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    if offset < 0 or not 1 <= limit <= 100:
        raise HTTPException(status_code=422, detail="Use offset >= 0 and a page size between 1 and 100.")
    rows = db.query(CreditTransaction).join(CreditWallet, CreditWallet.id == CreditTransaction.wallet_id).filter(
        CreditWallet.user_id == user.id).order_by(CreditTransaction.created_at.desc()).offset(offset).limit(limit + 1).all()
    return {"transactions": [{"id": row.id, "transaction_type": row.transaction_type, "amount": row.amount,
                               "balance_after": row.balance_after, "reference_id": row.reference_id,
                               "description": row.description, "created_at": row.created_at} for row in rows[:limit]],
            "has_more": len(rows) > limit}


@router.post("/api/billing/checkout")
def checkout(payload: CheckoutRequest, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, str]:
    enforce_rate_limit(request, scope="billing-checkout", limit=5)
    current = db.query(Subscription).filter_by(user_id=user.id).filter(Subscription.status.in_({"active", "trialing", "past_due"})).first()
    if current:
        raise HTTPException(status_code=409, detail="An active subscription exists. Use the plan-change action or customer portal instead of creating a duplicate subscription.")
    catalog = get_plan_catalog(db)
    plan = catalog.get(payload.plan_key)
    if not plan or payload.plan_key == "free" or not plan.get("active", True):
        raise HTTPException(status_code=422, detail="Choose an active paid subscription plan.")
    price_id = str(plan.get("stripe_price_id", ""))
    if not price_id.startswith("price_"):
        raise HTTPException(status_code=503, detail="This plan needs a Stripe test-mode Price ID configured by an administrator.")
    response = _stripe_request("checkout/sessions", {
        "mode": "subscription", "line_items[0][price]": price_id, "line_items[0][quantity]": "1",
        "success_url": f"{settings.frontend_url.rstrip('/')}/dashboard/settings?checkout=success",
        "cancel_url": f"{settings.frontend_url.rstrip('/')}/dashboard/settings?checkout=cancelled",
        "client_reference_id": user.id, "metadata[user_id]": user.id, "metadata[plan_key]": payload.plan_key,
        "subscription_data[metadata][user_id]": user.id, "subscription_data[metadata][plan_key]": payload.plan_key,
    }, idempotency_key=f"checkout:{user.id}:{payload.plan_key}:{int(time.time() // 60)}")
    url = response.get("url")
    if not isinstance(url, str) or not url.startswith("https://checkout.stripe.com/"):
        raise HTTPException(status_code=502, detail="Stripe did not return a valid hosted checkout URL.")
    return {"checkout_url": url}


@router.post("/api/billing/change-plan")
def change_plan(payload: CheckoutRequest, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, str]:
    enforce_rate_limit(request, scope="billing-change-plan", limit=5)
    subscription = db.query(Subscription).filter_by(user_id=user.id).filter(Subscription.status.in_({"active", "trialing"})).order_by(Subscription.created_at.desc()).first()
    if not subscription or not subscription.provider_subscription_id:
        raise HTTPException(status_code=404, detail="No active Stripe subscription is available to change.")
    catalog = get_plan_catalog(db)
    plan = catalog.get(payload.plan_key)
    price_id = str((plan or {}).get("stripe_price_id", ""))
    if not plan or payload.plan_key == "free" or not plan.get("active", True) or not price_id.startswith("price_"):
        raise HTTPException(status_code=422, detail="Choose a configured active paid plan.")
    stripe_id = subscription.provider_subscription_id
    if not stripe_id.startswith("sub_"):
        raise HTTPException(status_code=409, detail="Subscription is not linked to a valid Stripe subscription.")
    details = _stripe_get(f"subscriptions/{stripe_id}")
    items = (details.get("items") or {}).get("data") or []
    if not items or not items[0].get("id"):
        raise HTTPException(status_code=502, detail="Stripe subscription has no updateable line item.")
    updated = _stripe_request(f"subscriptions/{stripe_id}", {
        "items[0][id]": str(items[0]["id"]), "items[0][price]": price_id,
        "proration_behavior": "create_prorations", "metadata[plan_key]": payload.plan_key,
        "metadata[user_id]": user.id,
    }, idempotency_key=f"plan-change:{subscription.id}:{payload.plan_key}:{int(time.time() // 60)}")
    db.add(AuditLog(actor_user_id=user.id, action="billing.plan.change.requested", target_type="subscription", target_id=subscription.id, details={"plan_key": payload.plan_key, "stripe_status": updated.get("status")}))
    db.commit()
    return {"status": str(updated.get("status", subscription.status)), "requested_plan_key": payload.plan_key}


@router.post("/api/billing/cancel")
def cancel_subscription(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    enforce_rate_limit(request, scope="billing-cancel", limit=3)
    subscription = db.query(Subscription).filter_by(user_id=user.id).filter(Subscription.status.in_({"active", "trialing", "past_due"})).order_by(Subscription.created_at.desc()).first()
    if not subscription or not subscription.provider_subscription_id:
        raise HTTPException(status_code=404, detail="No active Stripe subscription is available to cancel.")
    if not subscription.provider_subscription_id.startswith("sub_"):
        raise HTTPException(status_code=409, detail="Subscription is not linked to a valid Stripe subscription.")
    result = _stripe_request(f"subscriptions/{subscription.provider_subscription_id}", {"cancel_at_period_end": "true"},
                             idempotency_key=f"subscription-cancel:{subscription.id}")
    subscription.cancel_at_period_end = bool(result.get("cancel_at_period_end", True))
    db.add(AuditLog(actor_user_id=user.id, action="billing.subscription.cancel", target_type="subscription", target_id=subscription.id))
    db.commit()
    return {"cancel_at_period_end": subscription.cancel_at_period_end,
            "current_period_end": subscription.current_period_end}


@router.post("/api/billing/portal")
def customer_portal(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, str]:
    enforce_rate_limit(request, scope="billing-portal", limit=5)
    subscription = db.query(Subscription).filter_by(user_id=user.id).filter(Subscription.provider_customer_id.isnot(None)).order_by(Subscription.created_at.desc()).first()
    if not subscription:
        raise HTTPException(status_code=404, detail="No Stripe customer account is linked to this user.")
    response = _stripe_request("billing_portal/sessions", {"customer": subscription.provider_customer_id,
                                  "return_url": f"{settings.frontend_url.rstrip('/')}/dashboard/settings"},
                               idempotency_key=f"portal:{user.id}:{int(time.time() // 60)}")
    url = response.get("url")
    if not isinstance(url, str) or not url.startswith("https://billing.stripe.com/"):
        raise HTTPException(status_code=502, detail="Stripe did not return a valid portal URL.")
    return {"portal_url": url}


def _subscription_from_checkout(db: Session, event: dict[str, Any]) -> None:
    obj = event.get("data", {}).get("object", {})
    metadata = obj.get("metadata") or {}
    user_id, plan_key = metadata.get("user_id") or obj.get("client_reference_id"), metadata.get("plan_key")
    if not user_id or not plan_key or not obj.get("subscription"):
        return
    catalog = get_plan_catalog(db)
    if plan_key not in catalog:
        return
    row = db.query(Subscription).filter_by(provider_subscription_id=obj["subscription"]).first()
    if not row:
        row = Subscription(user_id=user_id, provider_subscription_id=obj["subscription"], plan_key=plan_key, status="active")
        db.add(row)
    row.user_id = user_id
    row.plan_key = plan_key
    row.status = "active"
    row.provider_customer_id = obj.get("customer")
    db.flush()
    pending = db.query(WebhookEvent).filter_by(provider="stripe", event_type="invoice.paid", processed_at=None).all()
    for pending_event in pending:
        if _process_invoice(db, (pending_event.payload.get("data") or {}).get("object", {}), pending_event.provider_event_id):
            pending_event.processed_at = datetime.now(timezone.utc)


def _process_invoice(db: Session, obj: dict[str, Any], event_id: str) -> bool:
    subscription_id = obj.get("subscription")
    row = db.query(Subscription).filter_by(provider_subscription_id=subscription_id).first() if subscription_id else None
    if row is None:
        return False
    catalog = get_plan_catalog(db)
    plan = catalog.get(row.plan_key)
    if not plan:
        return True
    invoice_id = str(obj.get("id") or event_id)
    if int(obj.get("amount_paid") or 0) > 0:
        db.add(Payment(user_id=row.user_id, provider_payment_id=invoice_id,
                       amount=int(obj.get("amount_paid") or 0) / 100,
                       currency=str(obj.get("currency") or "usd").upper()[:3], status="paid",
                       payment_metadata={"hosted_invoice_url": obj.get("hosted_invoice_url"), "invoice_pdf": obj.get("invoice_pdf")}))
    billing_reason = str(obj.get("billing_reason", ""))
    credits = int(plan.get("monthly_credits", 0)) if billing_reason in {"subscription_create", "subscription_cycle"} else 0
    if credits:
        add_credits(db, row.user_id, credits, "subscription_allocation", idempotency_key=f"stripe:invoice:{invoice_id}",
                    reference_id=invoice_id, description=f"Monthly {plan['name']} subscription credit allocation")
    row.status = "active"
    period_start = obj.get("period_start")
    period_end = obj.get("period_end")
    if period_start:
        row.current_period_start = datetime.fromtimestamp(int(period_start), timezone.utc)
    if period_end:
        row.current_period_end = datetime.fromtimestamp(int(period_end), timezone.utc)
    return True


def _apply_event(db: Session, event: dict[str, Any]) -> bool:
    kind = event.get("type", "")
    obj = event.get("data", {}).get("object", {})
    if kind == "checkout.session.completed":
        _subscription_from_checkout(db, event)
        return True
    if kind == "invoice.paid":
        return _process_invoice(db, obj, str(event.get("id", "")))
    if kind in {"customer.subscription.updated", "customer.subscription.deleted"}:
        subscription = db.query(Subscription).filter_by(provider_subscription_id=obj.get("id")).first()
        if subscription:
            subscription.status = "canceled" if kind.endswith("deleted") else str(obj.get("status", "active"))[:32]
            plan_key = (obj.get("metadata") or {}).get("plan_key")
            if plan_key in get_plan_catalog(db):
                subscription.plan_key = plan_key
            subscription.cancel_at_period_end = bool(obj.get("cancel_at_period_end", False))
            if obj.get("current_period_start"):
                subscription.current_period_start = datetime.fromtimestamp(int(obj["current_period_start"]), timezone.utc)
            if obj.get("current_period_end"):
                subscription.current_period_end = datetime.fromtimestamp(int(obj["current_period_end"]), timezone.utc)
        return True
    if kind in {"invoice.payment_failed", "payment_intent.payment_failed"}:
        sub_id = obj.get("subscription")
        subscription = db.query(Subscription).filter_by(provider_subscription_id=sub_id).first() if sub_id else None
        if subscription:
            subscription.status = "past_due"
        return True
    return True


@router.post("/api/billing/webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(default="", alias="Stripe-Signature"), db: Session = Depends(get_db)) -> dict[str, bool]:
    secret = settings.stripe_webhook_secret
    if not secret:
        raise HTTPException(status_code=503, detail="Stripe webhook signing secret is not configured.")
    raw = await request.body()
    if len(raw) > 1024 * 1024:
        raise HTTPException(status_code=413, detail="Webhook body is too large.")
    if not _verify_stripe_signature(raw, stripe_signature, secret):
        raise HTTPException(status_code=400, detail="Invalid Stripe webhook signature.")
    try:
        event = json.loads(raw)
        if not isinstance(event, dict) or not isinstance(event.get("id"), str) or not isinstance(event.get("type"), str):
            raise ValueError("invalid event")
        if event.get("livemode") is not False:
            raise ValueError("live-mode events are disabled")
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid Stripe webhook payload.") from exc
    row = db.query(WebhookEvent).filter_by(provider="stripe", provider_event_id=event["id"]).first()
    if row and row.processed_at:
        return {"received": True}
    if not row:
        row = WebhookEvent(provider="stripe", provider_event_id=event["id"], event_type=event["type"], payload=event)
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            return {"received": True}
    processed = _apply_event(db, event)
    if processed:
        row.processed_at = datetime.now(timezone.utc)
    db.commit()
    return {"received": True}


@router.get("/api/billing/history")
def billing_history(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.query(Payment).filter_by(user_id=user.id).order_by(Payment.created_at.desc()).limit(100).all()
    return {"payments": [{"id": row.id, "amount": str(row.amount), "currency": row.currency, "status": row.status,
                           "invoice_url": (row.payment_metadata or {}).get("hosted_invoice_url"),
                           "invoice_pdf": (row.payment_metadata or {}).get("invoice_pdf"), "created_at": row.created_at}
                          for row in rows]}


class PlanUpdate(BaseModel):
    plans: dict[str, dict[str, Any]]


@router.get("/api/admin/plans")
def admin_get_plans(admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"plans": get_plan_catalog(db)}


@router.put("/api/admin/plans")
def update_plans(payload: PlanUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.billing.plans import validate_plan_catalog
    from app.models.models import SystemConfiguration
    try:
        catalog = validate_plan_catalog(payload.plans)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    row = db.query(SystemConfiguration).filter_by(key="pricing_plans").first()
    if row is None:
        row = SystemConfiguration(key="pricing_plans", value={"plans": catalog}, description="Subscription plan catalog")
        db.add(row)
    else:
        row.value = {"plans": catalog}
    db.add(AuditLog(actor_user_id=admin.id, action="pricing.plans.update", target_type="configuration", target_id="pricing_plans"))
    db.commit()
    return {"plans": public_plans(catalog)}
