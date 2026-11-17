"""Record approvals and rollbacks of evaluated public Lean worker routes."""

from pathlib import Path

from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade():
    source = Path(__file__).resolve().parents[1] / "sql" / "lean_evolution.sql"
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute(source.read_text(encoding="utf-8"))


def downgrade():
    raise NotImplementedError("The migration preserves researcher-signed Lean route decisions.")
