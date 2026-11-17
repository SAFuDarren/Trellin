"""Expose durable Lean inquiry timing and coverage for researcher review."""

from pathlib import Path

from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade():
    source = Path(__file__).resolve().parents[1] / "sql" / "lean_inquiry_metrics.sql"
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute(source.read_text(encoding="utf-8"))


def downgrade():
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute("DROP FUNCTION rm.lean_inquiry_pass_metrics(uuid)")
