import pytest
from pydantic import ValidationError

from app.core.config import Settings


def production_settings(**overrides):
    values = {
        "_env_file": None,
        "app_env": "production",
        "secret_key": "p" * 48,
        "frontend_url": "https://app.example.invalid",
        "backend_url": "https://api.example.invalid",
        "database_url": "postgresql+psycopg://user:password@db.example.invalid/app",
        "redis_url": "rediss://cache.example.invalid:6379/0",
        "trusted_proxy_cidrs": "10.42.0.0/16",
        "storage_provider": "s3",
        "aws_s3_bucket": "clipforge-test",
        "smtp_host": "smtp.example.invalid",
        "smtp_from_email": "noreply@example.invalid",
    }
    values.update(overrides)
    return Settings(**values)


def test_production_configuration_accepts_secure_basics():
    assert production_settings().app_env == "production"


def test_normalizes_hosted_postgres_urls_to_psycopg3():
    assert Settings(_env_file=None, database_url="postgresql://user:pass@db.example.invalid/app").database_url == (
        "postgresql+psycopg://user:pass@db.example.invalid/app"
    )
    assert Settings(_env_file=None, database_url="postgres://user:pass@db.example.invalid/app").database_url == (
        "postgresql+psycopg://user:pass@db.example.invalid/app"
    )


def test_production_rejects_development_key_sqlite_and_insecure_frontend():
    with pytest.raises(ValidationError, match="unique SECRET_KEY"):
        production_settings(secret_key="development-only-change-me")
    with pytest.raises(ValidationError, match="requires PostgreSQL"):
        production_settings(database_url="sqlite:///./local.db")
    with pytest.raises(ValidationError, match="HTTPS origin"):
        production_settings(frontend_url="http://app.example.invalid")


def test_production_disables_live_stripe_credentials():
    with pytest.raises(ValidationError, match="Live Stripe credentials are disabled"):
        production_settings(stripe_secret_key="sk_live_not-a-real-key")
    with pytest.raises(ValidationError, match="Live Stripe credentials are disabled"):
        production_settings(stripe_publishable_key="pk_live_not-a-real-key")


def test_production_requires_https_frontend_origin_and_persistent_storage():
    with pytest.raises(ValidationError, match="HTTPS origin"):
        production_settings(frontend_url="https://app.example.invalid/dashboard")
    with pytest.raises(ValidationError, match="BACKEND_URL must be an HTTPS origin"):
        production_settings(backend_url="http://api.example.invalid")
    with pytest.raises(ValidationError, match="S3-compatible storage"):
        production_settings(storage_provider="local")
    with pytest.raises(ValidationError, match="AWS_S3_BUCKET"):
        production_settings(aws_s3_bucket="")
    with pytest.raises(ValidationError, match="AWS_ENDPOINT_URL must use HTTPS"):
        production_settings(aws_endpoint_url="http://objects.example.invalid")


def test_production_rejects_local_redis():
    with pytest.raises(ValidationError, match="requires a Redis URL"):
        production_settings(redis_url="redis://localhost:6379/0")


def test_production_requires_explicit_proxy_trust_ranges():
    with pytest.raises(ValidationError, match="actual ingress proxy"):
        production_settings(trusted_proxy_cidrs="127.0.0.1/32,::1/128,172.16.0.0/12")
    with pytest.raises(ValidationError, match="trust every address"):
        production_settings(trusted_proxy_cidrs="0.0.0.0/0")
    with pytest.raises(ValidationError, match="valid network ranges"):
        production_settings(trusted_proxy_cidrs="not-a-cidr")


def test_production_accepts_test_mode_stripe_credentials():
    settings = production_settings(
        stripe_secret_key="sk_test_placeholder",
        stripe_publishable_key="pk_test_placeholder",
    )
    assert settings.stripe_secret_key.startswith("sk_test_")
