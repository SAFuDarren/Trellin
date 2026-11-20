# SPDX-License-Identifier: Apache-2.0

from contextlib import contextmanager
import os
import secrets
from uuid import uuid4

import pytest


@contextmanager
def disposable_database():
    """Yield a new database on the explicitly selected test cluster."""
    dsn = os.environ.get("RM_TEST_DSN")
    if not dsn:
        pytest.fail("Set RM_TEST_DSN to an administrator URI on a disposable test cluster.", pytrace=False)

    import psycopg
    from psycopg import sql
    from sqlalchemy.engine import make_url

    url = make_url(dsn)
    if url.drivername != "postgresql":
        pytest.fail("RM_TEST_DSN requires a postgresql:// URI.", pytrace=False)
    name = f"rm_native_{uuid4().hex}"
    identifier = sql.Identifier(name)
    database_dsn = url.set(database=name).render_as_string(hide_password=False)

    with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} ENCODING 'UTF8' TEMPLATE template0").format(identifier))
        try:
            yield database_dsn
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(identifier))


@contextmanager
def disposable_project_logins(*, revision="head"):
    """Create a migrated database and a temporary login for each project capability."""
    from research_multiplier.storage.migrate import upgrade

    with disposable_database() as dsn:
        upgrade(dsn, revision)
        with project_logins(dsn) as logins:
            yield logins


@contextmanager
def project_logins(dsn):
    """Create fresh project logins for a disposable database at a selected revision."""
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as admin:
        logins = {}
        names = []
        try:
            for capability in ("human", "scheduler", "agent", "verifier"):
                login = f"rm_native_{capability}_{uuid4().hex}"
                identifier = sql.Identifier(login)
                password = secrets.token_urlsafe(24)
                admin.execute(sql.SQL("CREATE ROLE {} LOGIN IN ROLE {} PASSWORD {}").format(
                    identifier, sql.Identifier(f"rm_{capability}"), sql.Literal(password),
                ))
                names.append(identifier)
                logins[f"RM_{capability.upper()}_DSN"] = make_conninfo(dsn, user=login, password=password)
            yield logins
        finally:
            for identifier in names:
                admin.execute(sql.SQL("DROP ROLE {}").format(identifier))
