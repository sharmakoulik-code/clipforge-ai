import pytest
from pydantic import ValidationError

from app.core.config import Settings


def production_settings(**overrides):
    values = {
        "_env_file": None,
        "app_env": "production",
        "secret_key": "p" * 48,
        "frontend_url": "https://app.example.invalid",
        "database_url": "postgresql+psycopg://user:password@db.example.invalid/app",
        "redis_url": "rediss://cache.example.invalid:6379/0",
        "smtp_host": "smtp.example.invalid",
        "smtp_from_email": "noreply@example.invalid",
    }
    values.update(overrides)
    return Settings(**values)


def test_production_configuration_accepts_secure_basics():
    assert production_settings().app_env == "production"


def test_production_rejects_development_key_sqlite_and_insecure_frontend():
    with pytest.raises(ValidationError, match="unique SECRET_KEY"):
        production_settings(secret_key="development-only-change-me")
    with pytest.raises(ValidationError, match="requires PostgreSQL"):
        production_settings(database_url="sqlite:///./local.db")
    with pytest.raises(ValidationError, match="must use HTTPS"):
        production_settings(frontend_url="http://app.example.invalid")


def test_production_disables_live_stripe_credentials():
    with pytest.raises(ValidationError, match="Live Stripe credentials are disabled"):
        production_settings(stripe_secret_key="sk_live_not-a-real-key")
