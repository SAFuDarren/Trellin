"""Bind evidence to verified attempts and fence human review and retries."""

from pathlib import Path
import re

from alembic import op


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    sql = Path(__file__).resolve().parents[1] / "sql" / "engineering_integrity.sql"
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute(sql.read_text(encoding="utf-8"))


def downgrade():
    sql_dir = Path(__file__).resolve().parents[1] / "sql"
    prior_services = (sql_dir / "initial_services.sql").read_text(encoding="utf-8")
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute("DROP VIEW rm.shared_evidence")
        for function in ("enqueue_job", "issue_evidence", "review_evidence", "promote_claim"):
            pattern = rf"^CREATE FUNCTION rm\.{function}\(.*?^END \$\$;"
            original = re.search(pattern, prior_services, re.MULTILINE | re.DOTALL).group()
            cursor.execute(original.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
        cursor.execute("""
            REVOKE EXECUTE ON FUNCTION rm.submit_attempt(uuid, text, integer, jsonb) FROM rm_verifier;
            DROP FUNCTION rm.enqueue_stage(uuid, text, text, jsonb, jsonb, text);
            DROP FUNCTION rm.enqueue_job_internal(uuid, text, rm.job_kind, jsonb, jsonb, integer, text);
            DROP TRIGGER check_candidate_job ON rm.jobs;
            DROP FUNCTION rm.guard_candidate_job();
            DROP TRIGGER check_verification_attempt ON rm.attempts;
            DROP FUNCTION rm.guard_verification_attempt();
            DROP TRIGGER check_evidence_grade ON rm.evidence;
            DROP FUNCTION rm.guard_evidence_grade();
            DROP FUNCTION rm.can_promote_receipt(rm.evidence);
            DROP FUNCTION rm.has_bound_verification(rm.evidence, boolean);
            ALTER TABLE rm.evidence DROP COLUMN issuance_sha256, DROP COLUMN verification_attempt_id;
            ALTER TABLE rm.jobs DROP COLUMN operation_key;
            CREATE VIEW rm.shared_evidence WITH (security_barrier = true) AS
                SELECT e.* FROM rm.evidence e JOIN rm.claims c ON c.id = e.claim_id
                WHERE e.verdict = 'verified' AND e.diff_status = 'exact' AND c.sensitivity = 'public';
            GRANT SELECT ON rm.shared_evidence TO rm_scheduler;
        """)
