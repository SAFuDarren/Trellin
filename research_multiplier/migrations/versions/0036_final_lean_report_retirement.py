"""Enforce withdrawal of the report bound to a final Lean check."""

from pathlib import Path

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade():
    script = Path(__file__).resolve().parents[1] / "sql" / "final_lean_report_retirement.sql"
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute(script.read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Restore a pre-upgrade backup to recover the previous proof retirement policy.")
