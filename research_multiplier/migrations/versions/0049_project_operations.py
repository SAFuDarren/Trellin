"""Meter model dispatches and bind Lean worker outcomes to the acquired run."""

from pathlib import Path

from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade():
    directory = Path(__file__).resolve().parents[1] / "sql"
    with op.get_bind().connection.cursor() as cursor:
        for name in ("project_model_budget.sql", "lean_worker_operations.sql"):
            cursor.execute((directory / name).read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Restore a pre-upgrade backup to preserve project operating records.")
