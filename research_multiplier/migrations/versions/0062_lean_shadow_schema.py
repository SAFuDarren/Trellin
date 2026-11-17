"""Read the exact database revision with the Lean shadow verifier role."""
from alembic import op

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE FUNCTION rm.lean_shadow_schema_revision() RETURNS text
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp AS $$
            SELECT (SELECT version_num::text FROM public.rm_alembic_version)
        $$;
        REVOKE ALL ON FUNCTION rm.lean_shadow_schema_revision() FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION rm.lean_shadow_schema_revision() TO rm_verifier;
    """)


def downgrade():
    op.execute("DROP FUNCTION rm.lean_shadow_schema_revision()")
