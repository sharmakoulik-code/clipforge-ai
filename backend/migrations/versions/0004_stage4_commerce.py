"""Add atomic credit reservations, user preferences, and audit history.

Revision ID: 0004_stage4_commerce
Revises: 0003_ai_processing
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_stage4_commerce"
down_revision = "0003_ai_processing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "credit_reservations",
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("consumed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="reserved"),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount > 0", name="ck_credit_reservation_amount_positive"),
        sa.CheckConstraint("consumed >= 0 AND consumed <= amount", name="ck_credit_reservation_consumed_range"),
        sa.ForeignKeyConstraint(["job_id"], ["processing_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", name="uq_credit_reservation_job"),
    )
    op.create_index("ix_credit_reservations_user_id", "credit_reservations", ["user_id"])
    op.create_table(
        "user_preferences",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("preferences", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_user_preferences_user"),
    )
    op.create_table(
        "audit_logs",
        sa.Column("actor_user_id", sa.String(length=36), nullable=True),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("target_type", sa.String(length=80), nullable=True),
        sa.Column("target_id", sa.String(length=160), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_actor_user_id", "audit_logs", ["actor_user_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_target_id", "audit_logs", ["target_id"])
    with op.batch_alter_table("credit_wallets") as batch_op:
        batch_op.create_check_constraint("ck_credit_wallet_balance_nonnegative", "balance >= 0")
        batch_op.create_check_constraint("ck_credit_wallet_reserved_nonnegative", "reserved >= 0")


def downgrade() -> None:
    with op.batch_alter_table("credit_wallets") as batch_op:
        batch_op.drop_constraint("ck_credit_wallet_reserved_nonnegative", type_="check")
        batch_op.drop_constraint("ck_credit_wallet_balance_nonnegative", type_="check")
    op.drop_index("ix_audit_logs_target_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_action", table_name="audit_logs")
    op.drop_index("ix_audit_logs_actor_user_id", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_table("user_preferences")
    op.drop_index("ix_credit_reservations_user_id", table_name="credit_reservations")
    op.drop_table("credit_reservations")
