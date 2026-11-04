from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class BaseModel(Base):
    __abstract__ = True
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)


class User(BaseModel):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(160))
    auth_provider: Mapped[str] = mapped_column(String(40), default="local", nullable=False)
    provider_subject: Mapped[str | None] = mapped_column(String(255), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class UserSession(BaseModel):
    __tablename__ = "user_sessions"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PasswordResetToken(BaseModel):
    __tablename__ = "password_reset_tokens"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Project(BaseModel):
    __tablename__ = "projects"
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String(2048))
    source_video_key: Mapped[str | None] = mapped_column(String(1024))
    source_filename: Mapped[str | None] = mapped_column(String(512))
    source_metadata: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)


class Transcript(BaseModel):
    __tablename__ = "transcripts"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    language: Mapped[str | None] = mapped_column(String(16))
    text: Mapped[str | None] = mapped_column(Text)
    segments: Mapped[list | None] = mapped_column(JSON)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)


class Clip(BaseModel):
    __tablename__ = "clips"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False)
    transcript_id: Mapped[str | None] = mapped_column(ForeignKey("transcripts.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    start_seconds: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    end_seconds: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    status: Mapped[str] = mapped_column(String(32), default="suggested", nullable=False)
    output_video_key: Mapped[str | None] = mapped_column(String(1024))
    metadata_json: Mapped[dict | None] = mapped_column(JSON)


class ProcessingJob(BaseModel):
    __tablename__ = "processing_jobs"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False)
    requested_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    job_type: Mapped[str] = mapped_column(String(64), default="repurpose", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True, nullable=False)
    current_stage: Mapped[str | None] = mapped_column(String(64))
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    last_completed_stage: Mapped[str | None] = mapped_column(String(64))
    worker_id: Mapped[str | None] = mapped_column(String(160))
    worker_task_id: Mapped[str | None] = mapped_column(String(160))
    worker_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    failure_class: Mapped[str | None] = mapped_column(String(32))
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    options: Mapped[dict | None] = mapped_column(JSON)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProcessingStage(BaseModel):
    __tablename__ = "processing_stages"
    job_id: Mapped[str] = mapped_column(ForeignKey("processing_jobs.id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    input_data: Mapped[dict | None] = mapped_column(JSON)
    output_data: Mapped[dict | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("job_id", "sequence", name="uq_processing_stage_sequence"),)


class ProcessingCheckpoint(BaseModel):
    __tablename__ = "processing_checkpoints"
    job_id: Mapped[str] = mapped_column(ForeignKey("processing_jobs.id", ondelete="CASCADE"), index=True, nullable=False)
    stage_name: Mapped[str] = mapped_column(String(64), nullable=False)
    checkpoint_key: Mapped[str] = mapped_column(String(160), nullable=False)
    data: Mapped[dict | None] = mapped_column(JSON)
    artifact_key: Mapped[str | None] = mapped_column(String(1024))
    __table_args__ = (UniqueConstraint("job_id", "checkpoint_key", name="uq_job_checkpoint_key"),)


class CreditWallet(BaseModel):
    __tablename__ = "credit_wallets"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    balance: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reserved: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    __table_args__ = (CheckConstraint("balance >= 0", name="ck_credit_wallet_balance_nonnegative"),
                      CheckConstraint("reserved >= 0", name="ck_credit_wallet_reserved_nonnegative"))


class CreditReservation(BaseModel):
    __tablename__ = "credit_reservations"
    job_id: Mapped[str] = mapped_column(ForeignKey("processing_jobs.id", ondelete="CASCADE"), unique=True, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    consumed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="reserved", nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("amount > 0", name="ck_credit_reservation_amount_positive"),
                      CheckConstraint("consumed >= 0 AND consumed <= amount", name="ck_credit_reservation_consumed_range"))


class CreditTransaction(BaseModel):
    __tablename__ = "credit_transactions"
    wallet_id: Mapped[str] = mapped_column(ForeignKey("credit_wallets.id", ondelete="CASCADE"), index=True, nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    transaction_type: Mapped[str] = mapped_column(String(48), nullable=False)
    reference_id: Mapped[str | None] = mapped_column(String(128))
    idempotency_key: Mapped[str | None] = mapped_column(String(160), unique=True)
    description: Mapped[str | None] = mapped_column(String(512))


class Subscription(BaseModel):
    __tablename__ = "subscriptions"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    provider_subscription_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    provider_customer_id: Mapped[str | None] = mapped_column(String(255), index=True)
    plan_key: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Payment(BaseModel):
    __tablename__ = "payments"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    provider_payment_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    payment_metadata: Mapped[dict | None] = mapped_column(JSON)


class ClipEdit(BaseModel):
    __tablename__ = "clip_edits"
    clip_id: Mapped[str] = mapped_column(ForeignKey("clips.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    edit_data: Mapped[dict] = mapped_column(JSON, nullable=False)
    __table_args__ = (UniqueConstraint("clip_id", "revision", name="uq_clip_edit_revision"),)


class ClipRenderVersion(BaseModel):
    __tablename__ = "clip_render_versions"
    clip_id: Mapped[str] = mapped_column(ForeignKey("clips.id", ondelete="CASCADE"), index=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    output_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    render_settings: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    validation_data: Mapped[dict | None] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("clip_id", "version", name="uq_clip_render_version"),)


class WebhookEvent(BaseModel):
    __tablename__ = "webhook_events"
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("provider", "provider_event_id", name="uq_provider_webhook_event"),)


class UsageRecord(BaseModel):
    __tablename__ = "usage_records"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    metric: Mapped[str] = mapped_column(String(80), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict | None] = mapped_column(JSON)


class UserPreference(BaseModel):
    __tablename__ = "user_preferences"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    preferences: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class AuditLog(BaseModel):
    __tablename__ = "audit_logs"
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    action: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(80))
    target_id: Mapped[str | None] = mapped_column(String(160), index=True)
    details: Mapped[dict | None] = mapped_column(JSON)


class SystemConfiguration(BaseModel):
    __tablename__ = "system_configurations"
    key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    value: Mapped[dict] = mapped_column(JSON, nullable=False)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    description: Mapped[str | None] = mapped_column(String(512))


class SocialProvider(BaseModel):
    __tablename__ = "social_providers"
    slug: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    oauth_supported: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    publishing_supported: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    approval_required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    availability_note: Mapped[str] = mapped_column(String(512), nullable=False)
    limits_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class SocialAccount(BaseModel):
    __tablename__ = "social_accounts"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    provider_id: Mapped[str] = mapped_column(ForeignKey("social_providers.id", ondelete="RESTRICT"), index=True, nullable=False)
    external_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="connected", nullable=False, index=True)
    account_metadata: Mapped[dict | None] = mapped_column(JSON)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("user_id", "provider_id", "external_account_id", name="uq_social_account_owner_provider_external"),)


class OAuthCredential(BaseModel):
    __tablename__ = "oauth_credentials"
    social_account_id: Mapped[str] = mapped_column(ForeignKey("social_accounts.id", ondelete="CASCADE"), unique=True, nullable=False)
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    scopes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    token_type: Mapped[str] = mapped_column(String(32), default="Bearer", nullable=False)
    last_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SocialOAuthState(BaseModel):
    __tablename__ = "social_oauth_states"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    provider_id: Mapped[str] = mapped_column(ForeignKey("social_providers.id", ondelete="CASCADE"), nullable=False)
    state_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    code_verifier: Mapped[str] = mapped_column(String(128), nullable=False)
    redirect_uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PublishingPost(BaseModel):
    __tablename__ = "publishing_posts"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    clip_id: Mapped[str | None] = mapped_column(ForeignKey("clips.id", ondelete="SET NULL"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    timezone: Mapped[str] = mapped_column(String(80), default="UTC", nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    __table_args__ = (UniqueConstraint("user_id", "idempotency_key", name="uq_publishing_post_owner_idempotency"),)


class PublishingTarget(BaseModel):
    __tablename__ = "publishing_targets"
    post_id: Mapped[str] = mapped_column(ForeignKey("publishing_posts.id", ondelete="CASCADE"), index=True, nullable=False)
    social_account_id: Mapped[str | None] = mapped_column(ForeignKey("social_accounts.id", ondelete="SET NULL"), index=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("social_providers.id", ondelete="RESTRICT"), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False, index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    platform_options: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    external_post_id: Mapped[str | None] = mapped_column(String(255))
    external_post_url: Mapped[str | None] = mapped_column(String(2048))
    last_error_code: Mapped[str | None] = mapped_column(String(80))
    last_error_message: Mapped[str | None] = mapped_column(String(1000))
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    analytics_json: Mapped[dict | None] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("post_id", "social_account_id", name="uq_publishing_target_post_account"),)


class PublishingSchedule(BaseModel):
    __tablename__ = "publishing_schedules"
    target_id: Mapped[str] = mapped_column(ForeignKey("publishing_targets.id", ondelete="CASCADE"), unique=True, nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    timezone: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="scheduled", nullable=False, index=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PublishingAttempt(BaseModel):
    __tablename__ = "publishing_attempts"
    target_id: Mapped[str] = mapped_column(ForeignKey("publishing_targets.id", ondelete="CASCADE"), index=True, nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(String(255))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(String(1000))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("target_id", "attempt_number", name="uq_publishing_attempt_number"),)


class PlatformMetadata(BaseModel):
    __tablename__ = "platform_metadata"
    target_id: Mapped[str] = mapped_column(ForeignKey("publishing_targets.id", ondelete="CASCADE"), index=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), unique=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    generation_model: Mapped[str | None] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(32), default="generated", nullable=False)
    __table_args__ = (UniqueConstraint("target_id", "version", name="uq_platform_metadata_version"),)


class PublishingLog(BaseModel):
    __tablename__ = "publishing_logs"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    post_id: Mapped[str | None] = mapped_column(ForeignKey("publishing_posts.id", ondelete="SET NULL"), index=True)
    target_id: Mapped[str | None] = mapped_column(ForeignKey("publishing_targets.id", ondelete="SET NULL"), index=True)
    action: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    details: Mapped[dict | None] = mapped_column(JSON)
