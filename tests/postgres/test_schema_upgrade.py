# SPDX-License-Identifier: Apache-2.0

import pytest

from research_multiplier.storage.migrate import migration_head, upgrade
from tests._native_db import disposable_database


pytestmark = pytest.mark.postgres


def assert_current_schema(connection):
    revision, = connection.execute("SELECT version_num FROM public.rm_alembic_version").fetchone()
    assert revision == migration_head()
    assert connection.execute("SELECT extname FROM pg_extension WHERE extname = 'vector'").fetchone() == ("vector",)


def test_empty_database_upgrades_to_current_schema():
    import psycopg

    with disposable_database() as dsn:
        upgrade(dsn)
        with psycopg.connect(dsn) as connection:
            assert_current_schema(connection)
            assert connection.execute("SELECT rm.current_database_id()").fetchone()[0] is not None


def test_upgrade_from_0068_preserves_database_identity():
    import psycopg

    with disposable_database() as dsn:
        upgrade(dsn, "0068")
        with psycopg.connect(dsn) as connection:
            assert connection.execute("SELECT version_num FROM public.rm_alembic_version").fetchone() == ("0068",)
            original_identity, = connection.execute("SELECT rm.current_database_id()").fetchone()

        upgrade(dsn)
        with psycopg.connect(dsn) as connection:
            assert_current_schema(connection)
            assert original_identity is not None
            assert connection.execute("SELECT rm.current_database_id()").fetchone() == (original_identity,)
