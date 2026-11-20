"""Save unsent proof notes for the authenticated local researcher."""

from pathlib import Path

from alembic import op

revision = "0082"
down_revision = "0081"
branch_labels = None
depends_on = None


def upgrade():
    source = Path(__file__).resolve().parents[1] / "sql" / "workbench_proof_drafts.sql"
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute(source.read_text(encoding="utf-8"))


def downgrade():
    raise NotImplementedError("Restore a backup to preserve locally saved proof drafts.")
