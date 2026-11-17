"""Keep formal statement approval and Lean verification separate from claim status."""

from pathlib import Path

from alembic import op


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    sql = Path(__file__).resolve().parents[1] / "sql" / "lean_checks.sql"
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute(sql.read_text(encoding="utf-8"))


def downgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM rm.lean_statements) THEN
                RAISE EXCEPTION 'cannot remove migration 0005 with saved Lean review history';
            END IF;
        END $$;
        DROP VIEW rm.lean_check_status;
        DROP VIEW rm.lean_statement_status;
        DROP FUNCTION rm.review_lean_check(uuid, text, text, text);
        DROP FUNCTION rm.record_lean_check(uuid, text, jsonb);
        DROP FUNCTION rm.confirm_lean_statement(uuid, integer, text, text, text);
        DROP FUNCTION rm.require_lean_review(uuid, boolean);
        DROP FUNCTION rm.propose_lean_statement(uuid, jsonb);
        DROP FUNCTION rm.lean_artifact_ref_valid(jsonb);
        DROP TABLE rm.lean_check_reviews;
        DROP TABLE rm.lean_checks;
        DROP TABLE rm.lean_statement_approvals;
        DROP TABLE rm.lean_statements;
    """)
