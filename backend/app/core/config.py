from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
        if not self.frontend_url.startswith("https://"):
            raise ValueError("Production FRONTEND_URL must use HTTPS.")
        if self.google_oauth_redirect_uri and not self.google_oauth_redirect_uri.startswith("https://"):
            raise ValueError("Production Google OAuth redirect URI must use HTTPS.")
        if self.database_url.startswith("sqlite"):
            raise ValueError("Production requires PostgreSQL; SQLite is for local development only.")
        if not self.redis_url.startswith(("redis://", "rediss://")):
            raise ValueError("Production requires a Redis URL for the job queue.")
        if self.stripe_secret_key.startswith("sk_live_"):
            raise ValueError("Live Stripe credentials are disabled; use test-mode keys until explicitly approved.")
        if not self.smtp_host or not self.smtp_from_email:
            raise ValueError("Production requires SMTP_HOST and SMTP_FROM_EMAIL for account recovery.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
