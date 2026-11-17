"""Publish supported native Lean receipts as reusable local theorems."""

from pathlib import Path

from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade():
    sql = Path(__file__).resolve().parents[1] / "sql" / "native_lean_replay.sql"
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute(sql.read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Restore a pre-upgrade backup to preserve native Lean replay records.")
