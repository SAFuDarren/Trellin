"""Let researchers withdraw a frozen claim, name its replacement, and restore it."""

from pathlib import Path

from alembic import op


revision = "0084"
down_revision = "0083"
branch_labels = None
depends_on = None


def upgrade():
    source = Path(__file__).resolve().parents[1] / "sql" / "claim_withdrawal.sql"
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute(source.read_text(encoding="utf-8"))


def downgrade():
    raise NotImplementedError("Restore a backup to preserve claim withdrawal decisions.")
