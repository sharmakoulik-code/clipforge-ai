from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session

from app.models.models import Subscription, SystemConfiguration

DEFAULT_PLANS: dict[str, dict[str, Any]] = {
    "free": {"name": "Free", "price_cents": 0, "currency": "usd", "monthly_credits": 60, "max_video_seconds": 300, "max_clips": 3, "max_concurrent_jobs": 1, "export_width": 720, "export_height": 1280, "watermark": True, "priority": "standard", "advanced_captions": False, "stripe_price_id": "", "active": True, "max_connected_accounts": 1, "max_scheduled_posts": 10, "monthly_publishing_volume": 5, "bulk_scheduling": False, "monthly_ai_metadata_generations": 10, "social_ai_metadata_credits": 1},
    "creator": {"name": "Creator", "price_cents": 999, "currency": "usd", "monthly_credits": 500, "max_video_seconds": 1800, "max_clips": 10, "max_concurrent_jobs": 2, "export_width": 1080, "export_height": 1920, "watermark": False, "priority": "standard", "advanced_captions": False, "stripe_price_id": "", "active": True, "max_connected_accounts": 3, "max_scheduled_posts": 100, "monthly_publishing_volume": 100, "bulk_scheduling": False, "monthly_ai_metadata_generations": 100, "social_ai_metadata_credits": 1},
    "pro": {"name": "Pro", "price_cents": 2499, "currency": "usd", "monthly_credits": 1500, "max_video_seconds": 7200, "max_clips": 20, "max_concurrent_jobs": 4, "export_width": 1080, "export_height": 1920, "watermark": False, "priority": "priority", "advanced_captions": True, "stripe_price_id": "", "active": True, "max_connected_accounts": 10, "max_scheduled_posts": 500, "monthly_publishing_volume": 500, "bulk_scheduling": True, "monthly_ai_metadata_generations": 500, "social_ai_metadata_credits": 1},
    "business": {"name": "Business", "price_cents": 9999, "currency": "usd", "monthly_credits": 6000, "max_video_seconds": 14400, "max_clips": 40, "max_concurrent_jobs": 10, "export_width": 1080, "export_height": 1920, "watermark": False, "priority": "priority", "advanced_captions": True, "stripe_price_id": "", "active": True, "max_connected_accounts": 50, "max_scheduled_posts": 5000, "monthly_publishing_volume": 5000, "bulk_scheduling": True, "monthly_ai_metadata_generations": 2000, "social_ai_metadata_credits": 1},
}


def get_plan_catalog(db: Session) -> dict[str, dict[str, Any]]:
    row = db.query(SystemConfiguration).filter_by(key="pricing_plans").first()
    if not row:
        return deepcopy(DEFAULT_PLANS)
    configured = row.value.get("plans") if isinstance(row.value, dict) else None
    return validate_plan_catalog(configured) if isinstance(configured, dict) else deepcopy(DEFAULT_PLANS)


def validate_plan_catalog(value: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if set(value) != set(DEFAULT_PLANS):
        raise ValueError("Plan catalog must include exactly free, creator, pro, and business plans.")
    validated: dict[str, dict[str, Any]] = {}
    for key, defaults in DEFAULT_PLANS.items():
        plan = value[key]
        if not isinstance(plan, dict):
            raise ValueError(f"Invalid {key} plan configuration.")
        normalized = {**defaults, **plan}
        for name in ("price_cents", "monthly_credits", "max_video_seconds", "max_clips", "max_concurrent_jobs", "export_width", "export_height", "max_connected_accounts", "max_scheduled_posts", "monthly_publishing_volume", "monthly_ai_metadata_generations", "social_ai_metadata_credits"):
            if isinstance(normalized[name], bool) or not isinstance(normalized[name], int) or normalized[name] < 0:
                raise ValueError(f"{key}.{name} must be a non-negative integer.")
        if not isinstance(normalized["name"], str) or not normalized["name"].strip() or len(normalized["name"]) > 80:
            raise ValueError(f"{key}.name must be 1–80 characters.")
        if key != "free" and normalized["price_cents"] <= 0:
            raise ValueError(f"{key}.price_cents must be positive.")
        if key == "free" and normalized["price_cents"] != 0:
            raise ValueError("The free plan must have a zero price.")
        if abs(normalized["export_width"] / max(1, normalized["export_height"]) - 9 / 16) > 0.01:
            raise ValueError(f"{key} export dimensions must be portrait 9:16.")
        if not isinstance(normalized["watermark"], bool) or not isinstance(normalized["advanced_captions"], bool) or not isinstance(normalized["bulk_scheduling"], bool) or not isinstance(normalized["active"], bool):
            raise ValueError(f"{key} feature flags must be boolean.")
        if not isinstance(normalized["currency"], str) or len(normalized["currency"]) != 3 or not normalized["currency"].isalpha():
            raise ValueError(f"{key}.currency must be a three-letter code.")
        if not isinstance(normalized["stripe_price_id"], str) or len(normalized["stripe_price_id"]) > 255:
            raise ValueError(f"{key}.stripe_price_id is invalid.")
        validated[key] = normalized
    return validated


def get_user_plan(db: Session, user_id: str) -> tuple[str, dict[str, Any]]:
    catalog = get_plan_catalog(db)
    subscription = db.query(Subscription).filter_by(user_id=user_id).order_by(Subscription.created_at.desc()).first()
    if subscription and subscription.status in {"active", "trialing"} and subscription.plan_key in catalog:
        key = subscription.plan_key
        return key, catalog[key]
    return "free", catalog["free"]


def public_plans(catalog: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: value for key, value in plan.items() if key != "stripe_price_id"} | {"key": key}
            for key, plan in catalog.items() if plan.get("active", True)]
