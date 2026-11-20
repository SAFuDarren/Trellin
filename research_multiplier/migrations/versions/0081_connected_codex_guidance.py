"""Permit explicitly released node guidance on a signed Codex proof task."""

from pathlib import Path

from alembic import op

revision = "0081"
down_revision = "0080"
branch_labels = None
depends_on = None


def upgrade():
    source = Path(__file__).resolve().parents[1] / "sql" / "connected_codex_guidance.sql"
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute(source.read_text(encoding="utf-8"))


def downgrade():
    raise NotImplementedError("Restore a backup to preserve released notes and proof receipts.")
