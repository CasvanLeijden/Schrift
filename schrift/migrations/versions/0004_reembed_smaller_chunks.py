"""Embed every note again with smaller chunks that fit the model's input length

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-14
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE notes SET embedded_hash = NULL")


def downgrade() -> None:
    pass
