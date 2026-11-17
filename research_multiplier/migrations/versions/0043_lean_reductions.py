"""Persist independently checked routes to a frozen theorem."""

from pathlib import Path

from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade():
    scripts = Path(__file__).resolve().parents[1] / "sql"
    with op.get_bind().connection.cursor() as cursor:
        for name in ("lean_reductions.sql", "lean_lemma_provenance.sql"):
            cursor.execute((scripts / name).read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Restore a pre-upgrade backup to preserve verified reductions.")
