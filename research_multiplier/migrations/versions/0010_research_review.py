"""Archive literature provenance and expose research review tasks."""

from pathlib import Path

from alembic import op


revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    path = Path(__file__).resolve().parents[1] / "sql" / "research_review.sql"
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute(path.read_text(encoding="utf-8"))


def downgrade():
    op.execute("""
        DROP VIEW rm.research_review_inbox, rm.novelty_review_status, rm.literature_source_status;
        DROP FUNCTION rm.record_research_understanding(uuid, text, text, text),
            rm.record_novelty_review(uuid, uuid, text, uuid[], jsonb, text, text),
            rm.confirm_literature_source(uuid, text, text, text),
            rm.register_literature_source(uuid, jsonb);
        DROP TABLE rm.research_understanding, rm.novelty_reviews,
            rm.literature_source_confirmations, rm.literature_sources;
    """)
