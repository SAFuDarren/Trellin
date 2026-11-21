"""Record authorized project tasks, execution attempts, and human reviews."""

from pathlib import Path

from alembic import op


revision = "0083"
down_revision = "0082"
branch_labels = None
depends_on = None


def upgrade():
    source = Path(__file__).resolve().parents[1] / "sql" / "agent_work.sql"
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute(source.read_text(encoding="utf-8"))


def downgrade():
    raise NotImplementedError("Restore a backup to preserve agent work and its review history.")
