"""Production schema and capability groups."""
from pathlib import Path

from alembic import op


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    assets = Path(__file__).resolve().parents[1] / "sql"
    # The migration transaction owns these unparameterized SQL scripts.
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
        for filename in ("initial_schema.sql", "initial_services.sql"):
            cursor.execute((assets / filename).read_text(encoding="utf-8"))


def downgrade():
    # Extension and NOLOGIN groups may be shared by other workspaces.
    op.execute("DROP SCHEMA rm CASCADE")
