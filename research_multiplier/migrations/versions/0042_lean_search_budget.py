"""Limit public Lean search dispatches across routes of one frozen claim."""

from pathlib import Path

from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade():
    script = Path(__file__).resolve().parents[1] / "sql" / "lean_search_budget.sql"
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute(script.read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Restore a pre-upgrade backup to preserve search budget history.")
