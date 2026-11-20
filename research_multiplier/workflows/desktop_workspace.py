"""Prepare a newly chosen Trellin workspace on an existing local database service."""

import os
from pathlib import Path
import sys
import unicodedata

from research_multiplier.storage import credentials, migrate, paths, project_archive
from research_multiplier.workflows import agent_guide, workbench_guidance
from research_multiplier.workflows.setup import Setup


_PROOF_NOTES_REVISION = "0081"
_UPGRADABLE_REVISIONS = frozenset(f"{revision:04d}" for revision in range(70, 81))


class WorkspaceCreationError(ValueError):
    """Fixed launcher error. Driver responses and connection strings stay private."""


class WorkspaceUpgradeError(ValueError):
    """Safe error code for the native project upgrade operation."""


def _upgrade_status(dsn, workspace):
    import psycopg

    from research_multiplier.storage.postgres import Store

    with Store() as store:
        installed = workbench_guidance.connected_installed(store, "human")
    expected = paths.database_id(workspace), paths.workspace_id(workspace)
    with psycopg.connect(dsn, connect_timeout=5, options="-c default_transaction_read_only=on -c statement_timeout=10000") as connection:
        state = connection.execute("""
            SELECT database_id, workspace_id, (SELECT version_num FROM public.rm_alembic_version)
            FROM rm.current_workspace_binding()
        """).fetchone()
    if state is None or state[:2] != expected or expected != (store.database, store.workspace_id):
        raise WorkspaceUpgradeError("unbound")
    return state[2], installed


def upgrade_project_database(workspace):
    """Verify an archive before upgrading the explicitly selected and bound project."""
    if not paths.strict_workspace() or paths.require_workspace() != workspace:
        raise WorkspaceUpgradeError("workspace")
    dsn = os.environ.get("RM_MIGRATION_DSN", "").strip()
    if not dsn:
        raise WorkspaceUpgradeError("login-missing")
    migrate.migration_url(dsn)
    revision, installed = _upgrade_status(dsn, workspace)
    if revision == _PROOF_NOTES_REVISION and installed:
        return None
    if revision not in _UPGRADABLE_REVISIONS or installed or migrate.migration_head() != _PROOF_NOTES_REVISION:
        raise WorkspaceUpgradeError("upgrade-unsupported")

    try:
        backup = project_archive.backup()["directory"]
        verified = project_archive.verify(backup)
        if verified["schema_revision"] != revision or verified["database_id"] != str(paths.database_id(workspace)):
            raise ValueError("The archive does not match the selected database revision.")
    except Exception:
        raise WorkspaceUpgradeError("upgrade-backup") from None

    migrate.upgrade(dsn, _PROOF_NOTES_REVISION)
    if _upgrade_status(dsn, workspace) != (_PROOF_NOTES_REVISION, True):
        raise WorkspaceUpgradeError("upgrade-failed")
    return backup


class _DesktopSetup(Setup):
    def say(self, text=""):
        # Desktop launch has a single structured reply, with no setup logs.
        pass


def select_workspace(location):
    """Open the selected workspace or the dedicated child of a research folder."""
    try:
        selected = Path(location)
        if not selected.is_absolute() or ".." in selected.parts:
            raise ValueError
        parent = selected.resolve(strict=True)
        if not parent.is_dir():
            raise ValueError
        marker = parent / paths.WORKSPACE_FILE
        if marker.exists() or marker.is_symlink():
            # An explicitly selected workspace must pass the normal open checks.
            if marker.is_symlink() or not marker.is_file():
                raise ValueError
            return parent
        child = parent / "Trellin"
        occupied = child.is_symlink() or child.exists()
    except (OSError, RuntimeError, ValueError):
        raise WorkspaceCreationError("workspace") from None

    if occupied:
        try:
            if (child.is_symlink() or not (child / paths.WORKSPACE_FILE).is_file()
                    or paths.workspace_details(child) != child):
                raise ValueError
        except (OSError, ValueError):
            raise WorkspaceCreationError("workspace-occupied") from None
        return child
    return create_workspace(child)


def _new_destination(location):
    selected = Path(location)
    name = selected.name
    if (not selected.is_absolute() or ".." in selected.parts or not name or name.startswith(".")
            or name.strip() != name or any(char in ":\\" or unicodedata.category(char).startswith("C") for char in name)):
        raise ValueError
    parent = selected.parent.resolve(strict=True)
    # macOS presents these system aliases as ordinary directories in the picker.
    parent_spelling = selected.parent
    for alias in (Path("/tmp"), Path("/var")):
        system_path = Path("/private") / alias.name
        if parent_spelling.is_relative_to(alias) and parent.is_relative_to(system_path):
            parent_spelling = system_path / parent_spelling.relative_to(alias)
            break
    if parent_spelling != parent or not parent.is_dir():
        raise ValueError

    target = parent / name
    paths._reject_program_path(target)
    if (target.exists() or target.is_symlink() or paths._workspace_above(parent) is not None
            or any(target.is_relative_to(item) for item in Path(sys.executable).resolve().parents
                   if item.suffix == ".app")):
        raise ValueError
    return target


def _local_endpoint():
    socket = os.environ.get("RM_DESKTOP_PG_SOCKET", "/tmp")
    port = os.environ.get("RM_DESKTOP_PG_PORT", "5432")
    if (not Path(socket).is_absolute() or "\x00" in socket or "," in socket or not Path(socket).is_dir()
            or not port.isascii() or not port.isdecimal() or not 0 < int(port) < 65536):
        raise ValueError
    return socket, int(port)


def create_workspace(location, *, minimal=False):
    """Provision and bind a new leaf directory, leaving an existing project untouched."""
    try:
        destination = _new_destination(location)
    except (OSError, RuntimeError, ValueError):
        raise WorkspaceCreationError("create-path") from None

    for key in tuple(os.environ):
        if key.startswith("PG") or key in (*credentials.KEYS, "RM_ADMIN_DSN", "RM_DATA_ROOT", "RM_ARTIFACT_ROOT"):
            os.environ.pop(key, None)

    try:
        host, port = _local_endpoint()
        setup = _DesktopSetup(destination, host=host, port=port, skip_lean=True, interactive=False)
        admin = setup.admin_connection()
    except Exception:
        raise WorkspaceCreationError("create-postgres") from None

    with admin:
        try:
            setup.check_server(admin)
        except Exception:
            raise WorkspaceCreationError("create-postgres") from None

        # Reserve the exact leaf atomically after preflight. Never use setup's
        # normal existing-directory mode for desktop creation.
        try:
            destination.mkdir(mode=0o700)
        except OSError:
            raise WorkspaceCreationError("create-path") from None

        try:
            paths.initialize_workspace(destination, minimal=minimal)
            agent_guide.install(destination)
            workspace_id = paths.workspace_id(destination)
            login = credentials.location(workspace_id)
            if login.exists() or login.is_symlink():
                raise ValueError
            with setup.fresh_database(admin) as values:
                from research_multiplier.storage.postgres import bind_workspace

                with paths.use_workspace(destination, strict=True):
                    os.environ.update(values)
                    bind_workspace(destination)
                credentials.write(workspace_id, values)
        except Exception:
            # Keep an incomplete folder intact. A subsequent Create must use a
            # different destination, and startup still requires a valid binding.
            raise WorkspaceCreationError("create-failed") from None

    return destination
