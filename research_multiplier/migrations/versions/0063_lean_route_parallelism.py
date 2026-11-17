"""Sign a bounded Lean route portfolio and enforce its call allowance."""
from importlib.resources import files

from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade():
    script = files("research_multiplier").joinpath("migrations", "sql", "lean_route_parallelism.sql")
    op.get_bind().exec_driver_sql(script.read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Restore a backup to preserve signed Lean route budgets and their history.")
