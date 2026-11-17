"""Record paired shadow experiments and human-authorized production model changes."""

from pathlib import Path

from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade():
    sql = Path(__file__).resolve().parents[1] / "sql" / "shadow_evolution.sql"
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute(sql.read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Restore a pre-upgrade backup to preserve shadow runs and model decisions.")
