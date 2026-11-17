"""Persist researcher-approved formalization packets and gate proof work."""

from pathlib import Path

from alembic import op


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    sql = Path(__file__).resolve().parents[1] / "sql" / "formalization_alignment.sql"
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute(sql.read_text(encoding="utf-8"))


def downgrade():
    # Dropping formalization history would let its existing claims enter proof
    # without the same researcher confirmation. Keep history until exported.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM rm.formalization_packets) THEN
                RAISE EXCEPTION 'cannot remove migration 0003 with saved formalization history';
            END IF;
        END $$;
        DROP TRIGGER check_aligned_freeze ON rm.claims;
        DROP TRIGGER check_formalization_job ON rm.jobs;
        DROP TRIGGER check_formalization_lease ON rm.jobs;
        DROP TRIGGER check_formalization_attempt ON rm.attempts;
        DROP TRIGGER check_formalization_evidence ON rm.evidence;
        DROP TRIGGER check_formalization_promotion ON rm.claims;
        DROP FUNCTION rm.guard_aligned_freeze();
        DROP FUNCTION rm.guard_formalization_job();
        DROP FUNCTION rm.guard_formalization_output();
        DROP FUNCTION rm.require_formalization_ready(uuid, jsonb);
        DROP FUNCTION rm.propose_formalization(jsonb, jsonb, uuid);
        DROP FUNCTION rm.confirm_formalization(uuid, integer, text, text[], text, text);
        DROP FUNCTION rm.request_clarification(uuid, text, text, text);
        DROP FUNCTION rm.resolve_clarification(uuid, text, text);
        DROP VIEW rm.formalization_status;
        DROP VIEW rm.formalization_clarification_status;
        DROP TABLE rm.clarification_resolutions;
        DROP TABLE rm.clarification_requests;
        DROP TABLE rm.formalization_approvals;
        DROP TABLE rm.formalization_packets;
        DROP TABLE rm.formalization_reviews;
    """)
