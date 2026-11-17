"""Add versioned research basis, proof dependencies and unified evidence."""

from pathlib import Path

from alembic import op


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    sql = Path(__file__).resolve().parents[1] / "sql" / "research_foundation.sql"
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute(sql.read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError(
        "Migration 0006 cannot be reversed in place. Restore the pre-upgrade database "
        "backup to preserve approved item versions and verification provenance."
    )
