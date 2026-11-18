"""Pair a PostgreSQL snapshot with a checked, portable project-file inventory."""

import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from datetime import datetime, timezone
from uuid import UUID

from research_multiplier.storage import paths
from .artifacts import write_json


FORMAT = 1
EXCLUDED = {".DS_Store", ".lake", "__pycache__"}


def _fingerprint(file):
    digest = hashlib.sha256()
    size = 0
    with file.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return {"sha256": digest.hexdigest(), "bytes": size}


def _tree(base, prefix):
    if base.is_symlink():
        raise ValueError("The project archive requires its category directories to be local to the workspace.")
    if not base.exists():
        return
    for parent, directories, names in os.walk(base):
        directories[:] = [name for name in directories if name not in EXCLUDED]
        parent = Path(parent)
        if any((parent / name).is_symlink() for name in directories):
            raise ValueError("Copy linked directories into the workspace before archiving.")
        for name in names:
            if name in EXCLUDED:
                continue
            file = parent / name
            if not file.is_file() or file.is_symlink():
                raise ValueError("The project archive requires regular files.")
            yield (PurePosixPath(prefix) / file.relative_to(base).as_posix()).as_posix(), file


def _project_files(root):
    files = {f"project/{paths.WORKSPACE_FILE}": root / paths.WORKSPACE_FILE}
    for marker in (paths.WORKSPACE_ID_FILE, paths.DATABASE_FILE):
        file = root / marker
        if file.is_file():
            files[f"project/{marker}"] = file
    for name in ("inputs", "runs", "lean-project", "evaluation", "exports"):
        files.update(_tree(root / name, f"project/{name}"))
    files.update(_tree(paths.object_store(), "project/objects"))
    return files


def _migration_dsn():
    dsn = os.environ.get("RM_MIGRATION_DSN")
    if not dsn:
        raise ValueError("Set RM_MIGRATION_DSN to the authorized database backup or restore login.")
    return dsn


def _connect(dsn):
    try:
        import psycopg

        return psycopg.connect(dsn, autocommit=True, connect_timeout=5)
    except ImportError:
        raise ValueError("Install the PostgreSQL dependencies before archiving a project.") from None
    except Exception:
        raise ValueError("The configured database backup or restore login is unavailable.") from None


def _pg_tool(tool, arguments, dsn, pg_bin):
    from psycopg.conninfo import conninfo_to_dict

    executable = shutil.which(str(Path(pg_bin) / tool) if pg_bin else tool)
    if not executable:
        raise ValueError(f"Install a PostgreSQL client with {tool} or provide --pg-bin.")
    try:
        settings = conninfo_to_dict(dsn)
    except Exception:
        raise ValueError("Use valid PostgreSQL connection settings for project archives.") from None
    if "service" in settings or any("\n" in value or "\r" in value for value in settings.values()):
        raise ValueError("For project archives, provide PostgreSQL connection parameters directly as one line.")
    with TemporaryDirectory(prefix="rm-pg-service-") as directory:
        service = Path(directory) / "service.conf"
        service.touch(mode=0o600, exist_ok=False)
        service.write_text("[rm_project_archive]\n" + "".join(f"{key}={value}\n" for key, value in settings.items()),
                           encoding="utf-8")
        environment = {key: value for key, value in os.environ.items()
                       if key in {"PATH", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL", "TMPDIR", "TMP"}}
        environment["PGSERVICEFILE"] = str(service)
        try:
            completed = subprocess.run([executable, "--dbname", "service=rm_project_archive", "--no-password", *arguments],
                                       env=environment, capture_output=True, timeout=1800, check=False)
        except subprocess.TimeoutExpired:
            raise ValueError(f"{tool} exceeded the project archive time limit.") from None
        if completed.returncode:
            raise ValueError(f"{tool} did not complete. Check database permissions, client version and restore roles.")


def _copy(files, target):
    recorded = {}
    for relative, source in sorted(files.items()):
        path = target / relative
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(source, path)
        path.chmod(0o600)
        recorded[relative] = _fingerprint(path)
    return recorded


def _runtime():
    try:
        release = version("research-multiplier")
    except PackageNotFoundError:
        release = "source-tree"
    return {"package": release, "python": sys.version.split()[0]}


def backup(destination=None, *, pg_bin=None):
    root = paths.active_workspace()
    if root is None:
        raise ValueError("Select the project workspace to back up.")
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = paths.external_destination(destination if destination is not None else root / "backups" / name, "backups")
    if any(target.is_relative_to(parent) for parent in (
        root / "runs", root / "inputs", root / "lean-project", root / "evaluation", root / "exports",
        paths.object_store()
    )):
        raise ValueError("Store a project backup outside the directories being archived.")
    if target.exists():
        raise FileExistsError(f"Archive already exists: {target}")
    dsn = _migration_dsn()
    with _connect(dsn) as connection:
        with connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            exists, = connection.execute("SELECT to_regprocedure('rm.current_workspace_binding()') IS NOT NULL").fetchone()
            if not exists:
                raise ValueError("Use the migrated PostgreSQL database bound to this workspace.")
            identifier, attached, snapshot = connection.execute(
                "SELECT database_id, workspace_id, pg_export_snapshot() FROM rm.current_workspace_binding()"
            ).fetchone()
            bound = paths.database_id(root)
            if bound is None or identifier != bound or attached is None or attached != paths.workspace_id(root):
                raise ValueError("Bind the workspace and use its matching PostgreSQL backup login.")
            revision, = connection.execute("SELECT version_num FROM public.rm_alembic_version").fetchone()
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with TemporaryDirectory(dir=target.parent, prefix=".rm-project-") as directory:
                staging = Path(directory) / "backup"
                staging.mkdir(mode=0o700)
                files = _project_files(root)
                recorded = _copy(files, staging)
                dump = staging / "database.dump"
                _pg_tool("pg_dump", ["--format=custom", "--snapshot", snapshot, "--file", str(dump)], dsn, pg_bin)
                latest = _project_files(root)
                if set(latest) != set(recorded) or any(
                    _fingerprint(file) != recorded[name] for name, file in latest.items()
                ):
                    raise ValueError("Project files changed during backup. Wait for new work to settle and retry.")
                dump.chmod(0o600)
                recorded["database.dump"] = _fingerprint(dump)
                manifest = {"format_version": FORMAT, "database_id": str(identifier), "workspace_id": str(attached),
                            "schema_revision": revision, "runtime": _runtime(), "files": recorded}
                write_json(staging / "manifest.json", manifest, external=True)
                if target.exists():
                    raise FileExistsError(f"Archive already exists: {target}")
                staging.rename(target)
    return {"directory": str(target), "database_id": str(identifier), "files": len(recorded)}


def _manifest(source):
    archive = Path(source).resolve()
    manifest = json.loads((archive / "manifest.json").read_bytes())
    if (not isinstance(manifest, dict) or manifest.get("format_version") != FORMAT
            or not isinstance(manifest.get("files"), dict)):
        raise ValueError("Expected a supported project backup manifest.")
    try:
        identifier = UUID(manifest["database_id"])
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError("The project backup is missing a valid database identity.") from None
    listed = manifest["files"]
    for name, record in listed.items():
        path = PurePosixPath(name)
        original = archive / name
        if (path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name
                or (name != "database.dump" and (len(path.parts) < 2 or path.parts[0] != "project"))
                or not original.resolve().is_relative_to(archive) or original.is_symlink()
                or not original.is_file() or _fingerprint(original) != record):
            raise ValueError(f"Invalid project archive entry: {name}")
    if ("database.dump" not in listed or f"project/{paths.WORKSPACE_FILE}" not in listed
            or f"project/{paths.DATABASE_FILE}" not in listed
            or paths.database_id(archive / "project") != identifier):
        raise ValueError("The archive is missing its paired database or project binding.")
    if "workspace_id" in manifest and (
        f"project/{paths.WORKSPACE_ID_FILE}" not in listed
        or str(paths.workspace_id(archive / "project")) != manifest["workspace_id"]
    ):
        raise ValueError("The archive is missing its paired workspace identity.")
    return archive, manifest


def verify(source):
    archive, manifest = _manifest(source)
    return {"directory": str(archive), "database_id": manifest["database_id"],
            "schema_revision": manifest["schema_revision"], "files": len(manifest["files"])}


def restore(source, workspace, *, pg_bin=None):
    from research_multiplier.storage.migrate import migration_url, upgrade

    archive, manifest = _manifest(source)
    target = paths.external_destination(workspace)
    if target.exists():
        raise ValueError("Choose a new destination directory for the restored project.")
    dsn = _migration_dsn()
    try:
        migration_url(dsn)
    except ImportError:
        raise ValueError("Install the PostgreSQL dependencies before restoring a project.") from None
    except ValueError:
        raise ValueError("Use a postgresql:// URL in RM_MIGRATION_DSN to restore a project.") from None
    with _connect(dsn) as connection:
        existing, = connection.execute("""
            SELECT to_regnamespace('rm') IS NOT NULL OR EXISTS (
                SELECT 1 FROM pg_catalog.pg_tables
                WHERE schemaname !~ '^pg_' AND schemaname <> 'information_schema'
            )
        """).fetchone()
        if existing:
            raise ValueError("Restore into a fresh PostgreSQL database without existing user tables.")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with TemporaryDirectory(dir=target.parent, prefix=".rm-restore-") as directory:
        staging = paths.initialize_workspace(Path(directory) / "project")
        markers = {f"project/{name}" for name in (paths.WORKSPACE_FILE, paths.WORKSPACE_ID_FILE, paths.DATABASE_FILE)}
        files = {name: archive / name for name in manifest["files"] if name.startswith("project/") and name not in markers}
        copied = _copy(files, Path(directory))
        if any(record != manifest["files"][name] for name, record in copied.items()):
            raise ValueError("The archive changed before the project could be restored.")
        dump = Path(directory) / "database.dump"
        shutil.copyfile(archive / "database.dump", dump)
        if _fingerprint(dump) != manifest["files"]["database.dump"]:
            raise ValueError("The database dump changed before it could be restored.")
        _pg_tool("pg_restore", ["--single-transaction", "--exit-on-error", "--no-owner", str(dump)],
                 dsn, pg_bin)
        with _connect(dsn) as connection:
            restored, = connection.execute("SELECT rm.current_database_id()").fetchone()
            if str(restored) != manifest["database_id"]:
                raise ValueError("Restored database identity differs from the checked archive.")
        upgrade(dsn)
        with _connect(dsn) as connection:
            identity, = connection.execute("SELECT rm.reidentify_restored_database(%s,%s)",
                                           (restored, paths.ensure_workspace_id(staging))).fetchone()
        paths.bind_database(staging, identity)
        if target.exists():
            raise FileExistsError(f"Restore destination already exists: {target}")
        staging.rename(target)
    return {"workspace": str(target), "database_id": str(identity), "files": len(manifest["files"])}
