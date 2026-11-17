"""Record sealed evaluations and independent human version recommendations."""

from pathlib import Path

from alembic import op


revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    path = Path(__file__).resolve().parents[1] / "sql" / "fixed_evaluations.sql"
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute(path.read_text(encoding="utf-8"))


def downgrade():
    op.execute("""
        DROP VIEW rm.evaluation_report_status;
        DROP FUNCTION rm.approve_eval_report(uuid, text, text, text), rm.register_eval_report(jsonb);
        DROP FUNCTION rm.evaluation_gate(jsonb);
        DROP TABLE rm.evaluation_approvals, rm.evaluation_reports;
    """)
