"""Publish committed research milestones to the existing workspace event stream."""

from pathlib import Path

from alembic import op

revision = "0075"
down_revision = "0074"
branch_labels = None
depends_on = None


def upgrade():
    source = Path(__file__).resolve().parents[1] / "sql" / "research_workflow_events.sql"
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute(source.read_text(encoding="utf-8"))


def downgrade():
    raise NotImplementedError("Use a backup to preserve the workspace event log.")
