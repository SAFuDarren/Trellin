"""Trace exact replays of checked working lemmas across Lean statements."""

from pathlib import Path

from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade():
    directory = Path(__file__).resolve().parents[1] / "sql"
    with op.get_bind().connection.cursor() as cursor:
        for name in ("lean_working_reuse.sql", "lean_working_reuse_receipt.sql"):
            cursor.execute((directory / name).read_text(encoding="utf-8"))


def downgrade():
    # Keep existing replay lineage and receipt compatibility on downgrade.
    op.execute("DROP FUNCTION rm.lean_inquiry_base(uuid)")
    op.execute("ALTER FUNCTION rm.lean_inquiry_base_before_working_reuse(uuid) RENAME TO lean_inquiry_base")
    op.execute("DROP TRIGGER bind_working_lemma_replay ON rm.local_lean_lemmas")
    op.execute("DROP FUNCTION rm.capture_working_lemma_replay(), rm.lean_working_lemma_candidates(uuid,text[])")
    op.execute("DROP FUNCTION rm.lean_working_proposition_shape(text)")
