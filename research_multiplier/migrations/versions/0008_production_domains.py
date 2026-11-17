"""Register deterministic domain checkers and allow bound refutation receipts."""

from pathlib import Path

from alembic import op


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    sql = Path(__file__).resolve().parents[1] / "sql" / "production_domains.sql"
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute(sql.read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError(
        "Migration 0008 cannot be reversed in place. Restore the pre-upgrade database "
        "backup to preserve domain verification and refutation evidence."
    )
