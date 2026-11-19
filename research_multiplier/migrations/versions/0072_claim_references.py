"""Assign durable short references to claims and their graph nodes."""

from pathlib import Path

from alembic import op

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None


def upgrade():
    source = Path(__file__).resolve().parents[1] / "sql" / "claim_references.sql"
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute(source.read_text(encoding="utf-8"))


def downgrade():
    raise NotImplementedError("Use a backup to preserve assigned claim and node references.")
