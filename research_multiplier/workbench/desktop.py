"""Private launcher protocol between Trellin and its local workbench process."""

from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import sys
from threading import Thread

from research_multiplier.storage import credentials, paths
from research_multiplier.storage.postgres import DatabaseOperationError

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


def _open(workspace_path, resources):
    try:
        if not Path(workspace_path).is_absolute():
            raise ValueError
        with paths.use_workspace(workspace_path, strict=True):
            workspace = paths.require_workspace()
    except (OSError, ValueError):
        raise _StartError("workspace") from None

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


def run(workspace_path, lifetime, output):
    with ExitStack() as resources:
        try:
            server = _open(workspace_path, resources)
            warnings = [] if shutil.which("lean") else ["lean"]
        except _StartError as error:
            _reply(output, "error", code=error.args[0])
            return 2
        except _MissingAssets:
            _reply(output, "error", code="install")
            return 2
        except Exception:
            # Driver paths, SQL responses and other diagnostics may contain
            # credentials or research data. Only send a fixed code.
            _reply(output, "error", code="startup")
            return 2

        _reply(output, "ready", url=server.launch_url, warnings=warnings)
        Thread(target=_stop_on_disconnect, args=(server, lifetime), daemon=True).start()
        server.serve_forever(poll_interval=0.2)
        return 0


def main():
    if len(sys.argv) != 2:
        _reply(sys.stdout, "error", code="workspace")
        return 2
    try:
        return run(sys.argv[1], sys.stdin.buffer, sys.stdout)
    except Exception:
        # The launcher observes process termination after a successful
        # handshake. Never emit a second reply or dump a traceback.
        return 1


if __name__ == "__main__":
    sys.exit(main())
