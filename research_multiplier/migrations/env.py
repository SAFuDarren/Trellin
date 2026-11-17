from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool


if context.is_offline_mode():
    raise RuntimeError("Run Research Multiplier migrations using migrate.upgrade(dsn).")

engine = create_engine(context.config.attributes["rm_url"], poolclass=NullPool)
try:
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            version_table="rm_alembic_version",
            version_table_schema="public",
            transactional_ddl=True,
        )
        with context.begin_transaction():
            connection.exec_driver_sql("SELECT pg_advisory_xact_lock(hashtext('research-multiplier-migration'))")
            context.run_migrations()
finally:
    engine.dispose()
