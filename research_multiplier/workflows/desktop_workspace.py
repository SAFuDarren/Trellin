"""Prepare a newly chosen Trellin workspace on an existing local database service."""

import os
from pathlib import Path
import sys
import unicodedata

from research_multiplier.storage import credentials, paths
from research_multiplier.workflows import agent_guide
from research_multiplier.workflows.setup import Setup


class WorkspaceCreationError(ValueError):
    """Fixed launcher error. Driver responses and connection strings stay private."""


class _DesktopSetup(Setup):
    def say(self, text=""):
        # Desktop launch has a single structured reply, with no setup logs.
        pass


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


def create_workspace(location):
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
            paths.initialize_workspace(destination)
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
