"""Track recent logins and worker/job recovery metadata.

Revision ID: 0005_recovery_and_activity
Revises: 0004_stage4_commerce
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_recovery_and_activity"
down_revision = "0004_stage4_commerce"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_users_last_login_at", "users", ["last_login_at"])
    op.add_column("processing_jobs", sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("processing_jobs", sa.Column("last_completed_stage", sa.String(length=64), nullable=True))
    op.add_column("processing_jobs", sa.Column("worker_id", sa.String(length=160), nullable=True))
    op.add_column("processing_jobs", sa.Column("worker_task_id", sa.String(length=160), nullable=True))
    op.add_column("processing_jobs", sa.Column("worker_heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("processing_jobs", sa.Column("failure_class", sa.String(length=32), nullable=True))
    op.add_column("processing_jobs", sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_processing_jobs_worker_heartbeat_at", "processing_jobs", ["worker_heartbeat_at"])


def downgrade() -> None:
    op.drop_index("ix_processing_jobs_worker_heartbeat_at", table_name="processing_jobs")
    op.drop_column("processing_jobs", "dead_lettered_at")
    op.drop_column("processing_jobs", "failure_class")
    op.drop_column("processing_jobs", "worker_heartbeat_at")
    op.drop_column("processing_jobs", "worker_task_id")
    op.drop_column("processing_jobs", "worker_id")
    op.drop_column("processing_jobs", "last_completed_stage")
    op.drop_column("processing_jobs", "retry_count")
    op.drop_index("ix_users_last_login_at", table_name="users")
    op.drop_column("users", "last_login_at")
