"""Versioned PostgreSQL schema setup for a migration identity."""

from contextlib import contextmanager
from importlib.resources import as_file, files


@contextmanager
def _migration_config():
    from alembic.config import Config

    assets = files("research_multiplier").joinpath("migrations")
    with as_file(assets.joinpath("env.py")) as environment:
        config = Config()
        config.set_main_option("script_location", str(environment.parent).replace("%", "%%"))
        yield config


def migration_head():
    """Read the latest revision in the same package that applies migrations."""
    from alembic.script import ScriptDirectory

    with _migration_config() as config:
        return ScriptDirectory.from_config(config).get_current_head()


def migration_url(dsn):
    """Validate the DSN and return the URL used by Alembic."""
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import ArgumentError

    invalid = "Use a postgresql:// or postgresql+psycopg:// migration DSN."
    try:
        url = make_url(dsn)
    except (ArgumentError, ValueError):
        raise ValueError(invalid) from None
    if url.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise ValueError(invalid)
    return url.set(drivername="postgresql+psycopg")


def migration(dsn, command, revision):
    from alembic import command as alembic_command

    with _migration_config() as config:
        config.attributes["rm_url"] = migration_url(dsn)
        getattr(alembic_command, command)(config, revision)


def upgrade(dsn, revision="head"):
    """Apply migrations using a dedicated migration identity."""
    migration(dsn, "upgrade", revision)


def downgrade(dsn, revision="base"):
    """Roll back the research schema. A complete rollback deletes its data."""
    migration(dsn, "downgrade", revision)
