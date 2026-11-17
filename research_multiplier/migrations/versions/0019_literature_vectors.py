"""Index exact workspace document versions for pgvector similarity search."""

from pathlib import Path

from alembic import op


revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade():
    path = Path(__file__).resolve().parents[1] / "sql" / "literature_vectors.sql"
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute(path.read_text(encoding="utf-8"))


def downgrade():
    op.execute("""
        DROP FUNCTION rm.import_literature_vectors(text, text, integer, jsonb),
            rm.search_literature_vectors(text, text, integer, jsonb, integer, text);
        DROP VIEW rm.literature_embedding_documents;
        DROP TABLE rm.literature_vectors, rm.literature_vector_profiles;
    """)
