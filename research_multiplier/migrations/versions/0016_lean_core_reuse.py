"""Replay exact Lean Core theorems across reviewed claims."""

from pathlib import Path

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    script = Path(__file__).resolve().parents[1] / "sql" / "lean_core_reuse.sql"
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute(script.read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Restore a pre-upgrade backup to retain Lean Core theorem dependencies.")
