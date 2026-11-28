"""Private launcher protocol between Trellin and its local workbench process."""

from contextlib import ExitStack, contextmanager, suppress
import json
import os
from pathlib import Path
import shutil
import sys
from threading import Thread
from uuid import UUID

from research_multiplier.storage import credentials, paths
from research_multiplier.storage.managed_workspaces import uuid_text, workspace_registration
from research_multiplier.storage.postgres import DatabaseOperationError

from research_multiplier.workflows import release_updates
from research_multiplier.workflows.desktop_library import Library, LibraryError
from research_multiplier.workflows.desktop_workspace import (
    WorkspaceCreationError, WorkspaceUpgradeError, upgrade_project_database,
)

from .server import _SCHEMA_SQLSTATES, WorkbenchServer, _check_schema, _MissingAssets, _MissingSchema


class _StartError(Exception):
    pass


def _configure_database(workspace):
    try:
        workspace_id = paths.workspace_id(workspace)
        database_id = paths.database_id(workspace)
    except (OSError, ValueError):
        raise _StartError("unbound") from None
    if workspace_id is None or database_id is None:
        raise _StartError("unbound")

    # Only the selected workspace supplies connections for this process.
    for key in credentials.KEYS:
        os.environ.pop(key, None)
    try:
        login_file = credentials.load(workspace)
    except (OSError, ValueError):
        raise _StartError("login-invalid") from None
    if login_file is None or not os.environ.get("RM_HUMAN_DSN", "").strip():
        raise _StartError("login-missing")


@contextmanager
def _selected_workspace(workspace_path):
    with ExitStack() as resources:
        try:
            if not Path(workspace_path).is_absolute():
                raise ValueError
            resources.enter_context(paths.use_workspace(workspace_path, strict=True))
            workspace = paths.require_workspace()
        except (OSError, ValueError):
            raise _StartError("workspace") from None
        yield workspace


def _open(workspace_path, resources, *, expected_project=None):
    with _selected_workspace(workspace_path) as workspace:
        if expected_project is not None and paths.workspace_id(workspace) != expected_project:
            raise _StartError("storage-project")
        _configure_database(workspace)
    server = resources.enter_context(WorkbenchServer(workspace))
    # Stop accepting requests when the launcher leaves, then drain handlers
    # before the process exits so a review or guidance write can complete.
    server.daemon_threads = False
    try:
        server.read(_check_schema)
    except DatabaseOperationError as error:
        code = "schema" if error.state in _SCHEMA_SQLSTATES else "postgres"
        raise _StartError(code) from None
    except _MissingSchema:
        raise _StartError("schema") from None
    except (OSError, ValueError):
        raise _StartError("postgres") from None
    return server


def _reply(output, status, **payload):
    reply = json.dumps(dict(version=1, status=status, **payload))
    print(reply, file=output, flush=True)


def _upgrade_project(workspace_path, output):
    try:
        with _selected_workspace(workspace_path) as workspace:
            _configure_database(workspace)
            backup = upgrade_project_database(workspace)
    except (_StartError, WorkspaceUpgradeError) as error:
        _reply(output, "error", code=error.args[0])
        return 2
    except Exception:
        _reply(output, "error", code="upgrade-failed")
        return 2
    _reply(output, "ready", backup=backup)
    return 0


def _stop_on_disconnect(server, lifetime):
    # The parent owns the write end. Closing it explicitly or exiting the
    # launcher both end this one local session.
    try:
        while lifetime.read(4096):
            pass
    except OSError:
        pass
    finally:
        server.shutdown()


class _Failed(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


# A remembered project that cannot be reopened is not an error the researcher
# has to fix: open the library instead. Database, schema and installation
# problems still stop, because the library would hit them too.
_REOPEN_FALLBACK = {"workspace", "unbound", "login-missing", "login-invalid"}


def _reopen_falls_back(code):
    return code in _REOPEN_FALLBACK or (code.startswith("storage-") and code != "storage-busy")


def _legacy_target(legacy):
    if not isinstance(legacy, dict):
        return None, None, None
    project_id = None
    raw_id = legacy.get("projectId")
    if isinstance(raw_id, str):
        with suppress(LibraryError, ValueError):
            project_id = UUID(uuid_text(raw_id))
    workspace_path = None
    if project_id is None:
        raw_path = legacy.get("path")
        if isinstance(raw_path, str) and raw_path.startswith("/") and "\x00" not in raw_path:
            workspace_path = raw_path
    position = legacy.get("position") if isinstance(legacy.get("position"), dict) else None
    return project_id, workspace_path, position


def run(workspace_path, lifetime, output, *, select=False, library=None, new_name=None, project_id=None,
        reopen=False, legacy=None):
    legacy_position = None
    if reopen and library is not None:
        try:
            # Every launch ensures the default library exists and is repaired before restore (#285).
            library.ensure_ready()
            saved = library.settings()
        except LibraryError as error:
            _reply(output, "error", code=error.code)
            return 2
        if saved["last_project_id"] is not None:
            project_id = UUID(saved["last_project_id"])
        elif isinstance(legacy, dict):
            with suppress(LibraryError):
                library.import_legacy_positions(legacy.get("positions"))
            project_id, workspace_path, legacy_position = _legacy_target(legacy)
    reopening = (library is not None and not select and new_name is None
                 and (workspace_path is not None or project_id is not None))
    try:
        return _session(workspace_path, lifetime, output, select=select, library=library,
                        new_name=new_name, project_id=project_id, legacy_position=legacy_position)
    except _Failed as failure:
        if not (reopening and _reopen_falls_back(failure.code)):
            _reply(output, "error", code=failure.code)
            return 2
        missed = failure.code
    try:
        return _session(None, lifetime, output, library=library, reopen_failed=missed)
    except _Failed as failure:
        _reply(output, "error", code=failure.code)
        return 2


def _session(workspace_path, lifetime, output, *, select=False, library=None, new_name=None, project_id=None,
             reopen_failed=None, legacy_position=None):
    try:
        if library is not None and project_id is not None:
            workspace_path = library.resolve_project(str(project_id))
        elif library is not None and new_name is not None:
            workspace_path = library.create(new_name)
        elif library is not None and workspace_path is None:
            workspace_path = library.open_default()
        elif select:
            library = library or Library()
            workspace_path = library.open_folder(workspace_path)
        elif library is not None:
            workspace_path = library.resolve_existing(workspace_path)
    except WorkspaceCreationError as error:
        raise _Failed(error.args[0]) from None
    except LibraryError as error:
        raise _Failed(error.code) from None
    except Exception:
        raise _Failed("create-failed" if select else "startup") from None
    with ExitStack() as resources:
        try:
            server = _open(str(workspace_path), resources, expected_project=project_id)
            selection = {"workspace": str(server.workspace)} if select or library is not None else {}
            if library is not None:
                if workspace_registration(server.workspace) is not None:
                    project = library.describe(server.workspace)
                else:
                    try:
                        project = library.register_external(server.workspace)
                    except LibraryError as error:
                        if error.code not in {"storage-busy", "storage-unavailable", "storage-invalid"}:
                            raise
                        # Older projects can still open if the app library is offline.
                        project = None
                if project is not None:
                    selection["name"] = project["name"]
                    selection["project_id"] = project["id"]
                    if hasattr(library, "remember_project"):
                        try:
                            position = library.remember_project(project["id"])
                            if position is None and isinstance(legacy_position, dict):
                                with suppress(LibraryError, KeyError, TypeError):
                                    position = library.save_position(
                                        project["id"], legacy_position["claimId"], legacy_position["nodeId"],
                                    )
                        except LibraryError as error:
                            if error.code not in {"storage-busy", "storage-unavailable", "storage-invalid"}:
                                raise
                            position = None
                        if position is not None:
                            selection["position"] = position
                # Keep a recorded update preference from an earlier install.
                # Fresh native profiles store this setting in the app home.
                legacy = credentials.directory().parent / "release-updates.sqlite3"
                if not legacy.exists():
                    server.release_updates = release_updates.ReleaseUpdates(library.anchor / "Settings" / legacy.name)
            if reopen_failed is not None:
                selection["reopen_failed"] = reopen_failed
            warnings = [] if shutil.which("lean") else ["lean"]
        except _StartError as error:
            raise _Failed(error.args[0]) from None
        except _MissingAssets:
            raise _Failed("install") from None
        except LibraryError as error:
            raise _Failed(error.code) from None
        except Exception:
            # Driver paths, SQL responses and other diagnostics may contain
            # credentials or research data. Only send a fixed code.
            raise _Failed("startup") from None

        _reply(output, "ready", url=server.launch_url, warnings=warnings, **selection)
        Thread(target=_stop_on_disconnect, args=(server, lifetime), daemon=True).start()
        server.serve_forever(poll_interval=0.2)
        return 0


def _storage(library, args, output):
    if args == ["--storage-info"]:
        data = library.status()
    elif len(args) == 2 and args[0] == "--storage-resolve":
        data = {"workspace": str(library.resolve_project(args[1], verify_database=True))}
    elif len(args) == 2 and args[0] == "--storage-preview":
        plan = library.preview(args[1])
        data = {
            "root": plan["source"], "destination": plan["destination"], "confirmation": plan["sha256"],
            "project_count": plan["project_count"], "files": plan["files"], "bytes": plan["bytes"],
        }
    elif len(args) == 3 and args[0] == "--storage-rename":
        data = {"project": library.rename(args[1], args[2])}
    elif len(args) == 2 and args[0] == "--storage-remove":
        data = {"project": library.unregister(args[1])}
    elif len(args) == 4 and args[0] == "--storage-save-position":
        data = {"position": library.save_position(args[1], args[2], args[3])}
    elif len(args) == 3 and args[0] == "--storage-move":
        moved = library.move(args[1], args[2])
        data = {"root": moved["destination"], "backup": moved["backup"]}
    else:
        _reply(output, "error", code="storage-destination")
        return 2
    _reply(output, "ready", **data)
    return 0


def main():
    args = sys.argv[1:]
    try:
        if len(args) == 2 and args[0] == "--upgrade-project":
            return _upgrade_project(args[1], sys.stdout)
        if args and args[0].startswith("--storage-"):
            return _storage(Library(), args, sys.stdout)
        if args == ["--reopen"]:
            return run(None, sys.stdin.buffer, sys.stdout, library=Library(), reopen=True)
        if len(args) == 2 and args[0] == "--reopen-legacy":
            legacy = None
            with suppress(ValueError):
                legacy = json.loads(args[1])
            return run(None, sys.stdin.buffer, sys.stdout, library=Library(), reopen=True, legacy=legacy)
        if args == ["--library"]:
            return run(None, sys.stdin.buffer, sys.stdout, library=Library())
        if len(args) == 2 and args[0] == "--registered-project":
            return run(None, sys.stdin.buffer, sys.stdout, library=Library(), project_id=UUID(uuid_text(args[1])))
        if len(args) == 2 and args[0] == "--new-managed":
            return run(None, sys.stdin.buffer, sys.stdout, library=Library(), new_name=args[1])
        if len(args) == 2 and args[0] == "--select":
            return run(args[1], sys.stdin.buffer, sys.stdout, select=True, library=Library())
        if len(args) == 1 and not args[0].startswith("--"):
            return run(args[0], sys.stdin.buffer, sys.stdout, library=Library())
        _reply(sys.stdout, "error", code="workspace")
        return 2
    except LibraryError as error:
        _reply(sys.stdout, "error", code=error.code)
        return 2
    except Exception:
        # Leave the currently registered path intact on a storage error. The
        # launcher observes termination without exposing internal diagnostics.
        return 1


if __name__ == "__main__":
    sys.exit(main())
