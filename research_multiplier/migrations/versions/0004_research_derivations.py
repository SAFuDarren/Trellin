"""Persist exact flow derivations and link selected goals to exploration bases."""

from pathlib import Path
import re

from alembic import op


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    sql = Path(__file__).resolve().parents[1] / "sql" / "research_derivations.sql"
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute(sql.read_text(encoding="utf-8"))


def downgrade():
    sql = Path(__file__).resolve().parents[1] / "sql" / "formalization_alignment.sql"
    previous = sql.read_text(encoding="utf-8")
    proposal = re.search(r"^CREATE FUNCTION rm\.propose_formalization\(.*?^END \$\$;", previous, re.M | re.S).group()
    status = re.search(r"^CREATE VIEW rm\.formalization_status .*?\) pending;", previous, re.M | re.S).group()
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute("""
            DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM rm.flow_derivation_records) OR EXISTS (
                    SELECT 1 FROM rm.formalization_packets WHERE packet ->> 'mode' = 'exploration'
                ) THEN
                    RAISE EXCEPTION 'cannot remove migration 0004 with saved derivations or exploration history';
                END IF;
            END $$;
            DROP VIEW rm.verified_derivations;
            DROP FUNCTION rm.register_flow_derivations(uuid, uuid, jsonb);
            DROP FUNCTION rm.choose_exploration(uuid, integer, text, jsonb, jsonb);
            DROP VIEW rm.formalization_status;
            DROP TABLE rm.flow_derivation_records;
        """)
        cursor.execute(proposal.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
        cursor.execute("DROP FUNCTION rm.matches_exploration_premises(jsonb, jsonb)")
        cursor.execute("DROP TABLE rm.exploration_goal_links")
        cursor.execute(status)
        cursor.execute("GRANT SELECT ON rm.formalization_status TO rm_human, rm_scheduler, rm_verifier")
