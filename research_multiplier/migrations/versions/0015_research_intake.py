"""Review consolidated research intake and record literature retrieval provenance."""

from pathlib import Path

from alembic import op


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    path = Path(__file__).resolve().parents[1] / "sql" / "research_intake.sql"
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute(path.read_text(encoding="utf-8"))


def downgrade():
    op.execute("""
        DROP VIEW rm.research_review_inbox;
        ALTER VIEW rm.research_review_inbox_before_intake RENAME TO research_review_inbox;
        DROP VIEW rm.research_intake_status;
        DROP FUNCTION rm.link_literature_search_hit(uuid, integer, uuid),
            rm.record_literature_search(uuid, text, jsonb, jsonb),
            rm.confirm_research_intake(uuid, text, jsonb, text, text),
            rm.draft_research_intake(jsonb), rm.intake_exact_match(jsonb);
        DROP TABLE rm.literature_search_links, rm.literature_search_hits, rm.literature_searches,
            rm.research_intake_confirmations, rm.research_intake_drafts;
    """)
