"""Record execution metrics and expose recoverable task state."""

from pathlib import Path

from alembic import op


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    path = Path(__file__).resolve().parents[1] / "sql" / "runtime_records.sql"
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute(path.read_text(encoding="utf-8"))


def downgrade():
    op.execute("""
        DROP VIEW rm.research_task_status;
        DROP FUNCTION rm.cancel_research_job(uuid, text, text);
        DROP FUNCTION rm.submit_measured_attempt(uuid, text, integer, jsonb, jsonb);
        DROP FUNCTION rm.record_job_reuse(uuid);
        DROP FUNCTION rm.log_human_time(uuid, text, numeric);
        DROP TABLE rm.job_cancellations, rm.attempt_measurements, rm.human_time_entries;
        DROP TRIGGER stamp_research_job ON rm.jobs;
        DROP FUNCTION rm.stamp_research_job();
        ALTER TABLE rm.jobs DROP COLUMN ready_at, DROP COLUMN leased_at, DROP COLUMN result_reuses;
    """)
