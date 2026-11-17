"""Keep interpretation questions linked to the exact unconfirmed draft."""

from pathlib import Path

from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade():
    source = Path(__file__).resolve().parents[1] / "sql" / "target_clarifications.sql"
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute(source.read_text(encoding="utf-8"))


def downgrade():
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute("""
            DROP VIEW rm.research_actionable_review_inbox;
            ALTER VIEW rm.research_actionable_review_before_clarifications RENAME TO research_actionable_review_inbox;
            DROP FUNCTION rm.record_target_clarification(text,uuid,uuid,text,text,text,jsonb,text),
                rm.answer_target_clarification(uuid,jsonb,text,text), rm.complete_target_clarification(uuid,text);
            DROP VIEW rm.target_clarification_status;
            DROP TABLE rm.target_clarifications;
        """)
