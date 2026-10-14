"""Make CalDAV clients fetch every task again (reminders no longer carry note links)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-14
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO task_changes (user_id, caldav_name, deleted) "
        "SELECT user_id, caldav_name, 0 FROM tasks"
    )


def downgrade() -> None:
    pass
