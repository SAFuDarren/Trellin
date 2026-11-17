"""Continue approved Lean model work across worker passes."""

from pathlib import Path

from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade():
    source = Path(__file__).resolve().parents[1] / "sql" / "sustained_lean_inquiry.sql"
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute(source.read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Restore a pre-upgrade backup to preserve signed inquiry history.")
