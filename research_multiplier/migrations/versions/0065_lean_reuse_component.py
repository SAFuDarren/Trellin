"""Register cross-claim working proof discovery for Lean ablation."""

from alembic import op


revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        INSERT INTO rm.harness_components(name, version, layer, purpose, metric, retirement_condition, enabled)
        VALUES ('cross-claim-working-reuse', '1', 'scaffolding',
                'Discover independently checked working proofs from other claims and reverify under the destination scope.',
                '{"measure":"paired_expected_outcomes_calls_and_wall_time","gates":["unchanged_scope","exact_receipts"]}',
                'A measured held-out run matches exact results and trust checks at lower cost without cross-claim replay.',
                true);
    """)


def downgrade():
    op.execute("DELETE FROM rm.harness_components WHERE name = 'cross-claim-working-reuse' AND version = '1'")
