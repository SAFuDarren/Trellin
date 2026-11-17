"""Bind Lean refutations to the complete frozen proposition."""

from pathlib import Path

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade():
    script = Path(__file__).resolve().parents[1] / "sql" / "whole_lean_negation.sql"
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute(script.read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Restore a pre-upgrade backup to preserve evidence review history.")
