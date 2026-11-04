"""Persist transcript cache metadata and 0-100 viral scores.

Revision ID: 0003_ai_processing
Revises: 0002_sessions
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_ai_processing"
down_revision = "0002_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transcripts", sa.Column("metadata_json", sa.JSON(), nullable=True))
    with op.batch_alter_table("clips") as batch_op:
        batch_op.alter_column("score", existing_type=sa.Numeric(5, 4), type_=sa.Numeric(5, 2), existing_nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("clips") as batch_op:
        batch_op.alter_column("score", existing_type=sa.Numeric(5, 2), type_=sa.Numeric(5, 4), existing_nullable=True)
    op.drop_column("transcripts", "metadata_json")
