"""Keep stale interpretation conditions on hold and protect outbound proof inputs."""

from pathlib import Path

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade():
    directory = Path(__file__).resolve().parents[1] / "sql"
    with op.get_bind().connection.cursor() as cursor:
        for filename in ("condition_review_liveness.sql", "model_input_visibility.sql"):
            cursor.execute((directory / filename).read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Restore a pre-upgrade backup to preserve review and disclosure decisions.")
