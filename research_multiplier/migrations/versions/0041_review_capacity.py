"""Make research review notes and capacity available to bounded workers."""

from pathlib import Path

from alembic import op

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade():
    scripts = Path(__file__).resolve().parents[1] / "sql"
    with op.get_bind().connection.cursor() as cursor:
        for name in ("review_capacity.sql", "incident_regressions.sql", "lean_search_provenance.sql"):
            cursor.execute((scripts / name).read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Restore a pre-upgrade backup to preserve review policies and decisions.")
