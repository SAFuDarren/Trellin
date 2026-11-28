"""Persist immutable research Work offers and their exact linked inputs."""

from pathlib import Path

from alembic import op


revision = "0085"
down_revision = "0084"
branch_labels = None
depends_on = None


def upgrade():
    source = Path(__file__).resolve().parents[1] / "sql" / "research_issues.sql"
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute(source.read_text(encoding="utf-8"))


def downgrade():
    raise NotImplementedError("Restore a backup to preserve research Work offers.")
