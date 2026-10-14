"""Folders that stay out of CalDAV; the folder label moves behind the reminder title

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column("caldav_excluded", sa.JSON(), nullable=False, server_default="[]")
        )
    # Every reminder title changes ("[ZOKA] Offerte" becomes "Offerte [ZOKA]").
    op.execute(
        "INSERT INTO task_changes (user_id, caldav_name, deleted) "
        "SELECT user_id, caldav_name, 0 FROM tasks"
    )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("caldav_excluded")
