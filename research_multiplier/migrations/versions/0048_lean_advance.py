"""Compose checked native Lean theorems and resume genuine scope decisions."""

from pathlib import Path

from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade():
    directory = Path(__file__).resolve().parents[1] / "sql"
    with op.get_bind().connection.cursor() as cursor:
        for name in ("lean_composition.sql", "lean_progress_worker.sql"):
            cursor.execute((directory / name).read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Restore a pre-upgrade backup to preserve Lean advancement records.")
