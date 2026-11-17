"""Register disjoint, reviewed Lean evaluation corpora."""
from importlib.resources import files

from alembic import op

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade():
    script = files("research_multiplier").joinpath("migrations", "sql", "lean_eval_corpus.sql")
    op.get_bind().exec_driver_sql(script.read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Restore a backup to preserve reviewed Lean evaluation corpora.")
