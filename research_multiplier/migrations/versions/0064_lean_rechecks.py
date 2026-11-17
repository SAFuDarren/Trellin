"""Track evaluation triggers and isolated Lean regression outcomes."""

from pathlib import Path

from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade():
    script = Path(__file__).parents[1] / "sql" / "lean_rechecks.sql"
    op.execute(script.read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Dropping recorded evaluation triggers and regression outcomes is unsupported.")
