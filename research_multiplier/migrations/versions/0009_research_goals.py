"""Store approved research bases, alternative conjectures and scoped decisions."""

from pathlib import Path

from alembic import op


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    path = Path(__file__).resolve().parents[1] / "sql" / "research_goals.sql"
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute(path.read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Restore the pre-migration backup to downgrade research goal history.")
