import ipaddress
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _is_local_host(host: str | None) -> bool:
    if not host or host.lower() == "localhost" or host.lower().endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


class Settings(BaseSettings):
    app_name: str = "AI Video Repurposer API"
    app_env: str = "development"
    secret_key: str = Field(default="development-only-change-me", min_length=16)
    frontend_url: str = "http://localhost:3000"
    backend_url: str = "http://localhost:8000"
    database_url: str = "sqlite:///./clipforge.db"
    redis_url: str = "redis://localhost:6379/0"
    transcription_provider: str = "faster-whisper"
    whisper_model: str = "small"
    whisper_device: str = "auto"
    whisper_compute_type: str = "int8"
    whisper_model_cache: str = "./data/models"
    ai_provider: str = "openai"
    ai_model: str = ""
    processing_max_clips: int = 10
    processing_task_max_retries: int = 3
    processing_heartbeat_stale_seconds: int = 900
    processing_job_timeout_seconds: int = 14400
    celery_worker_concurrency: int = 2
    credits_per_source_minute: int = 1
    credits_per_rendered_clip: int = 2
    free_monthly_credits: int = 60
    processing_min_clip_seconds: int = 20
    processing_max_clip_seconds: int = 60
    video_sample_interval_seconds: float = 1.0
    render_width: int = 1080
    render_height: int = 1920
    render_fps: int = 30
    render_video_bitrate: str = "5M"
    storage_provider: str = "local"
    local_storage_path: str = "./data/uploads"
    max_upload_bytes: int = 2 * 1024 * 1024 * 1024
    trusted_proxy_cidrs: str = "127.0.0.1/32,::1/128,172.16.0.0/12"
    session_ttl_seconds: int = 14 * 24 * 60 * 60
    password_reset_ttl_seconds: int = 60 * 60
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_from_name: str = "AI Video Repurposer"
    smtp_starttls: bool = True
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    gemini_api_key: str = ""
    anthropic_api_key: str = ""
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_s3_bucket: str = ""
    aws_region: str = ""
    aws_endpoint_url: str = ""
    aws_s3_prefix: str = "clipforge"
    stripe_secret_key: str = ""
    stripe_publishable_key: str = ""
    stripe_webhook_secret: str = ""
    social_token_encryption_key: str = ""
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_redirect_uri: str = ""
    social_schedule_poll_seconds: int = 30

    @field_validator("database_url", mode="before")
    @classmethod
    def use_psycopg3_for_postgres(cls, value: str) -> str:
        """Normalize common hosted-Postgres URL schemes to the installed driver."""
        if isinstance(value, str):
            if value.startswith("postgres://"):
                return "postgresql+psycopg://" + value.removeprefix("postgres://")
            if value.startswith("postgresql://"):
                return "postgresql+psycopg://" + value.removeprefix("postgresql://")
        return value

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[3] / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @model_validator(mode="after")
    def validate_production_settings(self) -> "Settings":
        oauth_values = (self.google_oauth_client_id, self.google_oauth_client_secret, self.google_oauth_redirect_uri)
        if any(oauth_values) and not all(oauth_values):
            raise ValueError("Configure the Google OAuth client ID, secret, and redirect URI together.")
        if all(oauth_values) and not self.social_token_encryption_key:
            raise ValueError("SOCIAL_TOKEN_ENCRYPTION_KEY is required before enabling social OAuth.")
        if self.app_env.lower() not in {"production", "prod"}:
            return self
        if self.secret_key == "development-only-change-me" or len(self.secret_key) < 32:
            raise ValueError("Production requires a unique SECRET_KEY of at least 32 characters.")
        frontend = urlsplit(self.frontend_url)
        if (frontend.scheme != "https" or not frontend.netloc or frontend.username or frontend.password
                or frontend.path not in {"", "/"} or frontend.query or frontend.fragment
                or _is_local_host(frontend.hostname)):
            raise ValueError("Production FRONTEND_URL must be an HTTPS origin without a path, query, or fragment.")
        backend = urlsplit(self.backend_url)
        if (backend.scheme != "https" or not backend.netloc or backend.username or backend.password
                or backend.path not in {"", "/"} or backend.query or backend.fragment
                or _is_local_host(backend.hostname)):
            raise ValueError("Production BACKEND_URL must be an HTTPS origin without a path, query, or fragment.")
        try:
            trusted_networks = [ipaddress.ip_network(cidr.strip(), strict=False)
                                for cidr in self.trusted_proxy_cidrs.split(",") if cidr.strip()]
        except ValueError as exc:
            raise ValueError("TRUSTED_PROXY_CIDRS must contain valid network ranges.") from exc
        development_networks = {
            ipaddress.ip_network("127.0.0.1/32"),
            ipaddress.ip_network("::1/128"),
            ipaddress.ip_network("172.16.0.0/12"),
        }
        if set(trusted_networks) == development_networks:
            raise ValueError("Production requires TRUSTED_PROXY_CIDRS to match the actual ingress proxy network.")
        if not trusted_networks or any(network.prefixlen == 0 for network in trusted_networks):
            raise ValueError("Production TRUSTED_PROXY_CIDRS must not be empty or trust every address.")
        if self.google_oauth_redirect_uri and not self.google_oauth_redirect_uri.startswith("https://"):
            raise ValueError("Production Google OAuth redirect URI must use HTTPS.")
        database = urlsplit(self.database_url)
        if not self.database_url.startswith("postgresql+psycopg://") or _is_local_host(database.hostname):
            raise ValueError("Production requires PostgreSQL; SQLite is for local development only.")
        redis = urlsplit(self.redis_url)
        if (redis.scheme not in {"redis", "rediss"} or not redis.hostname
                or _is_local_host(redis.hostname)):
            raise ValueError("Production requires a Redis URL for the job queue.")
        if self.storage_provider not in {"s3", "s3-compatible"} or not self.aws_s3_bucket:
            raise ValueError("Production requires S3-compatible storage and AWS_S3_BUCKET.")
        if self.aws_endpoint_url and not self.aws_endpoint_url.startswith("https://"):
            raise ValueError("Production AWS_ENDPOINT_URL must use HTTPS.")
        if self.stripe_secret_key.startswith("sk_live_") or self.stripe_publishable_key.startswith("pk_live_"):
            raise ValueError("Live Stripe credentials are disabled; use test-mode keys until explicitly approved.")
        if self.stripe_secret_key and not self.stripe_secret_key.startswith("sk_test_"):
            raise ValueError("Production Stripe credentials must be test-mode keys.")
        if self.stripe_publishable_key and not self.stripe_publishable_key.startswith("pk_test_"):
            raise ValueError("Production Stripe credentials must be test-mode keys.")
        if not self.smtp_host or not self.smtp_from_email:
            raise ValueError("Production requires SMTP_HOST and SMTP_FROM_EMAIL for account recovery.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
