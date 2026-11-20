"""Publish remaining durable workspace changes through the same event log."""

from pathlib import Path

from alembic import op

revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None


def upgrade():
    sources = Path(__file__).resolve().parents[1] / "sql"
    with op.get_bind().connection.cursor() as cursor:
        for name in ("workspace_change_events.sql", "workbench_runtime_clock.sql"):
            cursor.execute((sources / name).read_text(encoding="utf-8"))


def downgrade():
    raise NotImplementedError("Use a backup to preserve the workspace event log.")
