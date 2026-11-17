"""Expose read-only previews from the withdrawal dependency graph."""

from pathlib import Path

from alembic import op

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None


def upgrade():
    source = Path(__file__).resolve().parents[1] / "sql" / "proof_graph.sql"
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute(source.read_text(encoding="utf-8"))


def downgrade():
    raise NotImplementedError("Use a backup made before enabling the proof graph.")
