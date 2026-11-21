"""Private device preferences with atomic updates across local app sessions."""

from contextlib import closing, contextmanager
import os
import sqlite3
import stat
import sys

from . import credentials
from .managed_workspaces import application_home


def settings_directory():
    if sys.platform == "darwin":
        return application_home() / "Settings"
    return credentials.directory().parent


@contextmanager
def private_database(file, *, write=False):
    if write:
        file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            descriptor = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
    try:
        info = file.lstat()
    except FileNotFoundError:
        if write:
            raise
        yield None
        return
    shared = os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077)
    if not stat.S_ISREG(info.st_mode) or shared:
        raise PermissionError("The settings file is not private.")
    mode = "rw" if write else "ro"
    uri = file.absolute().as_uri() + f"?mode={mode}"
    with closing(sqlite3.connect(uri, uri=True, timeout=2)) as database, database:
        if write:
            database.execute("BEGIN IMMEDIATE")
        yield database


def has_table(database, name):
    return database is not None and database.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ? AND type = 'table'", (name,),
    ).fetchone() is not None
