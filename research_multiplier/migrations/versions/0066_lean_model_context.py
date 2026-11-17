"""Authorize the provenance of derived Lean context for public model dispatch."""

from pathlib import Path

from alembic import op

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


def upgrade():
    source = Path(__file__).resolve().parents[1] / "sql" / "lean_model_context.sql"
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute(source.read_text(encoding="utf-8"))


def downgrade():
    raise NotImplementedError("This migration protects the provenance of outgoing model requests.")
