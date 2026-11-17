"""Serialize local and independent dispositions of the same route condition."""

from pathlib import Path

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade():
    script = Path(__file__).resolve().parents[1] / "sql" / "route_condition_disposition_fence.sql"
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute(script.read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Restore a pre-upgrade backup to recover the original route decision behavior.")
