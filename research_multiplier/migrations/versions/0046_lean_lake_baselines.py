"""Bind Lake rechecks to an immutable reviewed semantic baseline."""

from pathlib import Path

from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade():
    sql = Path(__file__).resolve().parents[1] / "sql" / "lean_lake_baselines.sql"
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute(sql.read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Restore a pre-upgrade backup to preserve exact Lake verification records.")
