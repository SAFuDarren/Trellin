"""Resolve runtime storage independently of the source directory."""

from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID, uuid4


WORKSPACE_FILE = ".research-multiplier.json"
WORKSPACE_ID_FILE = ".research-multiplier-workspace-id"
DATABASE_FILE = ".research-multiplier-database"
LOCAL_USER_FILE = ".research-multiplier-local-user-id"
_ACTIVE_WORKSPACE = ContextVar("research_multiplier_workspace", default=None)
_STRICT_WORKSPACE = ContextVar("research_multiplier_strict_workspace", default=False)
_PACKAGE_PARENT = Path(__file__).resolve().parents[2]
WORKSPACE_DIRECTORIES = ("inputs", "runs", "objects", "exports", "backups", "evaluation", "lean-project")
_MANAGED_DIRECTORIES = (*WORKSPACE_DIRECTORIES, "runs/offline", "runs/claims", "runs/lean", "evaluation/private",
                        "evaluation/private/collections", "evaluation/private/objects")


def _resolve(location):
    try:
        return Path(location).expanduser().resolve()
    except RuntimeError:
        raise ValueError(f"Unable to resolve the path {location}.") from None


def _reject_program_path(path):
    if path.is_relative_to(_PACKAGE_PARENT) or any(
        (parent / "pyproject.toml").is_file() and any(
            (parent / "research_multiplier" / marker).is_file()
            for marker in ("__main__.py", "paths.py")
        )
        for parent in (path, *path.parents)
    ):
        raise ValueError("Choose a location outside the Research Multiplier program directory.")


def _check_layout(root):
    for name in _MANAGED_DIRECTORIES:
        path = root / name
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ValueError(f"Workspace directory conflicts with {path}.")


def workspace_details(location):
    root = _resolve(location)
    _reject_program_path(root)
    marker = root / WORKSPACE_FILE
    if marker.is_symlink():
        raise ValueError(f"Use a real workspace settings file at {marker}.")
    try:
        data = json.loads(marker.read_bytes())
    except FileNotFoundError:
        raise ValueError(f"Initialize a research workspace at {root} with 'research init-workspace PATH'.") from None
    except (UnicodeError, ValueError):
        raise ValueError(f"Unsupported workspace settings in {marker}.") from None
    if (not isinstance(data, dict) or set(data) != {"format_version"}
            or type(data["format_version"]) is not int or data["format_version"] != 1):
        raise ValueError(f"Unsupported workspace settings in {root / WORKSPACE_FILE}.")
    return root


def initialize_workspace(location):
    root = _resolve(location)
    _reject_program_path(root)
    if _workspace_above(root.parent) is not None:
        raise ValueError("Choose a workspace outside existing research workspaces.")
    _check_layout(root)
    marker = root / WORKSPACE_FILE
    if marker.exists() or marker.is_symlink():
        workspace_details(root)
    workspace_id(root)
    database_id(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name in _MANAGED_DIRECTORIES:
        (root / name).mkdir(mode=0o700, exist_ok=True)
    _publish_marker(root, WORKSPACE_FILE, b'{"format_version": 1}\n')
    ensure_workspace_id(root)
    return root


def _workspace_above(location):
    resolved = _resolve(location)
    for candidate in (resolved, *resolved.parents):
        marker = candidate / WORKSPACE_FILE
        if marker.exists() or marker.is_symlink():
            return workspace_details(candidate)
    return None


def active_workspace():
    explicit = _ACTIVE_WORKSPACE.get()
    if explicit is not None:
        return explicit
    configured = os.environ.get("RM_DATA_ROOT")
    if configured:
        root = _resolve(configured)
        if strict_workspace() or (root / WORKSPACE_FILE).is_file():
            return workspace_details(root)
        return None
    return _workspace_above(Path.cwd())


def strict_workspace():
    return _STRICT_WORKSPACE.get()


def require_workspace():
    workspace = active_workspace()
    if workspace is None:
        raise ValueError("Choose a research workspace with 'research init-workspace PATH', then use --workspace PATH "
                         "or run inside that workspace.")
    return workspace


def _read_uuid_marker(workspace, name, label):
    if workspace is None:
        return None
    marker = Path(workspace) / name
    if marker.is_symlink() or (marker.exists() and not marker.is_file()):
        raise ValueError(f"The project {label} must be a regular file.")
    try:
        saved = marker.read_text(encoding="ascii").strip()
        return UUID(saved)
    except FileNotFoundError:
        return None
    except (UnicodeError, ValueError):
        raise ValueError(f"The project {label} is invalid.") from None


def database_id(workspace):
    return _read_uuid_marker(workspace, DATABASE_FILE, "database binding")


def workspace_id(workspace):
    return _read_uuid_marker(workspace, WORKSPACE_ID_FILE, "workspace identity")


def local_user_id(workspace):
    return _read_uuid_marker(workspace, LOCAL_USER_FILE, "local user identity")


def _publish_marker(workspace, name, data):
    temporary = None
    try:
        with NamedTemporaryFile(dir=workspace, prefix=".rm-bind-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, workspace / name)
        except FileExistsError:
            pass
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def ensure_workspace_id(workspace):
    root = workspace_details(workspace)
    existing = workspace_id(root)
    if existing is not None:
        return existing
    _publish_marker(root, WORKSPACE_ID_FILE, f"{uuid4()}\n".encode("ascii"))
    return workspace_id(root)


def bind_database(workspace, identifier):
    root = workspace_details(workspace)
    _publish_marker(root, DATABASE_FILE, f"{identifier}\n".encode("ascii"))
    if database_id(root) != identifier:
        raise ValueError("This research project is already bound to a different database.")


def bind_local_user(workspace, identifier):
    root = workspace_details(workspace)
    _publish_marker(root, LOCAL_USER_FILE, f"{identifier}\n".encode("ascii"))
    if local_user_id(root) != identifier:
        raise ValueError("This research project is already bound to a different local user.")


@contextmanager
def use_workspace(location, *, strict=False):
    selected = _ACTIVE_WORKSPACE.set(workspace_details(location)) if location is not None else None
    mode = _STRICT_WORKSPACE.set(True) if strict else None
    try:
        if strict:
            root = require_workspace()
            if selected is None:
                selected = _ACTIVE_WORKSPACE.set(root)
            _check_layout(root)
            override = os.environ.get("RM_ARTIFACT_ROOT")
            if override and _resolve(override) != root / "objects":
                raise ValueError("Use the selected workspace's objects/ directory. Clear the RM_ARTIFACT_ROOT override.")
        yield
    finally:
        if mode is not None:
            _STRICT_WORKSPACE.reset(mode)
        if selected is not None:
            _ACTIVE_WORKSPACE.reset(selected)


def data_root():
    workspace = active_workspace()
    if workspace is not None:
        return workspace
    configured = os.environ.get("RM_DATA_ROOT")
    if configured and not strict_workspace():
        root = _resolve(configured)
        _reject_program_path(root)
        return root
    return require_workspace()


def runs():
    return data_root() / "runs"


def offline_runs():
    workspace = active_workspace()
    return workspace / "runs" / "offline" if workspace is not None else runs()


def claim_runs():
    workspace = active_workspace()
    return workspace / "runs" / "claims" if workspace is not None else runs()


def lean_project():
    return data_root() / "lean-project"


def shared_artifacts(run_directory=None):
    if strict_workspace():
        workspace = require_workspace()
        if run_directory is not None:
            validate_project_read(run_directory)
        return workspace / "objects"
    # Explicit historical replay can read the run's own objects. All writes
    # still go through validate_project_write for the selected workspace.
    workspace = (_workspace_above(run_directory) if run_directory is not None else None) or active_workspace()
    if workspace is not None:
        return workspace / "objects"
    configured = os.environ.get("RM_ARTIFACT_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return None


def object_store():
    return shared_artifacts() or data_root() / "objects"


def validate_project_read(location):
    target = _resolve(location)
    owner = _workspace_above(target)
    if owner is not None and owner != require_workspace():
        raise ValueError("The selected path belongs to a different research workspace.")
    return target


def validate_project_write(location):
    if not strict_workspace() and active_workspace() is None:
        return Path(location)
    target = validate_project_read(location)
    root = require_workspace()
    _reject_program_path(target)
    if not target.is_relative_to(root):
        raise ValueError("Choose a write location within the selected research workspace.")
    return target


def category_path(location, category):
    root = require_workspace()
    target = validate_project_write(location)
    if not target.is_relative_to(root / category):
        raise ValueError(f"Choose a location under {root / category}.")
    return target


def external_destination(location, category=None):
    target = _resolve(location)
    _reject_program_path(target)
    owner = _workspace_above(target)
    if owner is not None and (owner != active_workspace() or category is None
                              or not target.is_relative_to(owner / category)):
        raise ValueError("Use the selected project's destination folder or a separate external location.")
    return target
