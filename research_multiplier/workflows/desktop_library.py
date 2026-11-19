"""Provision, register and relocate Trellin's user-owned library on a local Mac."""

from contextlib import contextmanager, suppress
import os
from pathlib import Path
import shutil
import stat
from tempfile import TemporaryDirectory
from uuid import uuid4

from research_multiplier.protocol.serialization import fingerprint
from research_multiplier.storage import credentials, paths
from research_multiplier.storage.managed_workspaces import (
    LibraryError, LOCATION_FILE, MANAGED_FILE, application_home, canonical_path, library_lock, library_root,
    publish_metadata, read_metadata, uuid_text, workspace_registration,
)
from research_multiplier.storage.project_archive import _fingerprint
from research_multiplier.workflows.desktop_workspace import WorkspaceCreationError, create_workspace


_REGISTRY = "registry.json"
_ROOT_INFO = {"format_version", "default_id", "default_slot", "projects"}


def _name(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 120 or not value.isprintable():
        raise LibraryError("storage-project")
    return value.strip()


def _directory(path):
    try:
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError
    except (OSError, ValueError):
        raise LibraryError("storage-unavailable") from None


def _workspace_path(root, row):
    return Path(row["path"]) if "path" in row else root / "Workspaces" / row["slot"]


def _project_markers(workspace):
    try:
        paths.workspace_details(workspace)
        identifier = paths.workspace_id(workspace)
        database = paths.database_id(workspace)
        if identifier is None or database is None:
            raise ValueError
    except (OSError, ValueError):
        raise LibraryError("storage-project") from None
    return str(identifier), str(database)


def _check_binding(workspace, row):
    """Read the workspace's saved role directly without changing other project logins."""
    if _project_markers(workspace) != (row["id"], row["database"]):
        raise LibraryError("storage-verification")
    try:
        import psycopg

        logins = credentials.read(row["id"])
        with psycopg.connect(logins["RM_HUMAN_DSN"], connect_timeout=5,
                             options="-c statement_timeout=10000 -c default_transaction_read_only=on") as connection:
            identity = connection.execute("SELECT database_id, workspace_id FROM rm.current_workspace_binding()").fetchone()
            if identity is None or tuple(map(str, identity)) != (row["database"], row["id"]):
                raise ValueError
    except Exception:
        # Driver diagnostics can contain credentials. Never send them to the UI.
        raise LibraryError("storage-database") from None


def _inventory(root):
    """Compare files, directory structure and permissions without following links."""
    files, directories = {}, {}
    try:
        _directory(root)
        for parent, children, names in os.walk(root, onerror=_scan_error):
            parent = Path(parent)
            for child in children:
                directory = parent / child
                info = directory.lstat()
                if not stat.S_ISDIR(info.st_mode):
                    raise LibraryError("storage-verification")
                directories[directory.relative_to(root).as_posix()] = stat.S_IMODE(info.st_mode)
            for name in names:
                file = parent / name
                info = file.lstat()
                if not stat.S_ISREG(info.st_mode):
                    raise LibraryError("storage-verification")
                files[file.relative_to(root).as_posix()] = {**_fingerprint(file), "mode": stat.S_IMODE(info.st_mode)}
    except OSError:
        raise LibraryError("storage-verification") from None
    return files, directories


def _scan_error(error):
    raise error


def _reject_storage_path(path):
    paths._reject_program_path(path)
    if any(parent.suffix.casefold() == ".app" for parent in (path, *path.parents)):
        raise ValueError


def _sync_file(path):
    descriptor = os.open(path, os.O_NOFOLLOW | os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sync_copy(staged, inventory):
    files, directories = inventory
    for relative in files:
        _sync_file(staged / relative)
    for relative in sorted(directories, key=lambda name: name.count("/"), reverse=True):
        _sync_file(staged / relative)
    _sync_file(staged)


def _destination(source, anchor, location):
    try:
        entered = Path(location)
        target = canonical_path(entered)
        if (target.is_relative_to(source) or source.is_relative_to(target) or anchor.is_relative_to(target)
                or target != canonical_path(entered.parent) / entered.name
                or target.exists() or target.is_symlink() or not target.parent.is_dir()
                or paths._workspace_above(target.parent) is not None):
            raise ValueError
        _reject_storage_path(target)
    except (OSError, TypeError, ValueError):
        raise LibraryError("storage-destination") from None
    return target


class Library:
    """One local registry. Older projects remain at their own paths."""

    def __init__(self, anchor=None):
        self.anchor = application_home(anchor)
        try:
            _reject_storage_path(self.anchor)
            if paths._workspace_above(self.anchor) is not None:
                raise ValueError
        except (OSError, ValueError):
            raise LibraryError("storage-destination") from None

    @contextmanager
    def _locked(self, *, exclusive=True):
        try:
            self.anchor.mkdir(mode=0o700, parents=True, exist_ok=True)
            with library_lock(self.anchor, exclusive=exclusive):
                yield
        except OSError:
            raise LibraryError("storage-unavailable") from None

    def _state(self, *, initialize=False):
        root = library_root(self.anchor, required=not initialize)
        if initialize and not (self.anchor / LOCATION_FILE).exists():
            if root != self.anchor / "Library" or root.is_symlink():
                raise LibraryError("storage-invalid")
            root.mkdir(mode=0o700, exist_ok=True)
            _directory(root)
            projects = root / "Workspaces"
            projects.mkdir(mode=0o700, exist_ok=True)
            _directory(projects)
            if not (root / _REGISTRY).exists():
                if any(projects.iterdir()) or {entry.name for entry in root.iterdir()} != {"Workspaces"}:
                    raise LibraryError("storage-invalid")
                publish_metadata(root / _REGISTRY, {"default_id": None, "default_slot": str(uuid4()), "projects": []})
            registry = self._read(root)
            if registry["projects"] or any(projects.iterdir()):
                raise LibraryError("storage-invalid")
            publish_metadata(self.anchor / LOCATION_FILE, {"root": str(root)})
            return root, registry
        return root, self._read(root)

    def _read(self, root):
        _directory(root)
        _directory(root / "Workspaces")
        try:
            data = read_metadata(root / _REGISTRY)
        except FileNotFoundError:
            raise LibraryError("storage-invalid") from None
        if set(data) != _ROOT_INFO or not isinstance(data["projects"], list):
            raise LibraryError("storage-invalid")
        default_slot = uuid_text(data["default_slot"])
        ids, slots, external = set(), set(), set()
        for row in data["projects"]:
            if not isinstance(row, dict) or set(row) not in (
                {"id", "database", "name", "slot"}, {"id", "database", "name", "path"},
            ):
                raise LibraryError("storage-invalid")
            identifier = uuid_text(row["id"])
            uuid_text(row["database"])
            _name(row["name"])
            if identifier in ids:
                raise LibraryError("storage-invalid")
            ids.add(identifier)
            if "slot" in row:
                slot = uuid_text(row["slot"])
                if slot in slots or (slot == default_slot) != (identifier == data["default_id"]):
                    raise LibraryError("storage-invalid")
                slots.add(slot)
            else:
                path = canonical_path(row["path"])
                if str(path) != row["path"] or path.is_relative_to(root) or path in external:
                    raise LibraryError("storage-invalid")
                external.add(path)
        if data["default_id"] is not None:
            if uuid_text(data["default_id"]) not in ids or default_slot not in slots:
                raise LibraryError("storage-invalid")
        return data

    @staticmethod
    def _project(root, row):
        return {"id": row["id"], "name": row["name"], "path": str(_workspace_path(root, row)),
                "managed": "slot" in row}

    def status(self):
        with self._locked():
            root, data = self._state(initialize=True)
            return {"root": str(root), "default_id": data["default_id"],
                    "projects": [self._project(root, row) for row in data["projects"]]}

    def _create(self, root, data, name, slot, *, default=False):
        workspace = root / "Workspaces" / slot
        if workspace.exists() or workspace.is_symlink():
            # Recover a completed but unpublished default after an interrupted start.
            if not default or workspace.is_symlink():
                raise LibraryError("storage-project")
            identifier, database = _project_markers(workspace)
            _check_binding(workspace, {"id": identifier, "database": database})
            if workspace_registration(workspace) not in (None, (self.anchor, slot)):
                raise LibraryError("storage-project")
        else:
            try:
                create_workspace(workspace, minimal=True)
            except WorkspaceCreationError as error:
                raise LibraryError(str(error)) from None
            identifier, database = _project_markers(workspace)
        publish_metadata(workspace / MANAGED_FILE, {"anchor": str(self.anchor), "slot": slot})
        data["projects"].append({"id": identifier, "database": database, "name": name, "slot": slot})
        if default:
            data["default_id"] = identifier
        publish_metadata(root / _REGISTRY, data)
        return workspace

    def open_default(self):
        with self._locked():
            root, data = self._state(initialize=True)
            if data["default_id"] is not None:
                return self._registered_path(root, self._project_row(data, data["default_id"]))
            return self._create(root, data, "Library", data["default_slot"], default=True)

    def create(self, name):
        name = _name(name)
        with self._locked():
            root, data = self._state(initialize=True)
            return self._create(root, data, name, str(uuid4()))

    def register_external(self, path):
        workspace = canonical_path(path)
        if workspace_registration(workspace) is not None:
            return
        identifier, database = _project_markers(workspace)
        with self._locked():
            root, data = self._state(initialize=True)
            if workspace.is_relative_to(root):
                raise LibraryError("storage-project")
            known = next((row for row in data["projects"] if row["id"] == identifier), None)
            if known is not None:
                if _workspace_path(root, known) != workspace or known["database"] != database:
                    raise LibraryError("storage-project")
                return self._project(root, known)
            name = workspace.parent.name if workspace.name == "Trellin" else workspace.name
            row = {"id": identifier, "database": database, "name": _name(name), "path": str(workspace)}
            data["projects"].append(row)
            publish_metadata(root / _REGISTRY, data)
            return self._project(root, row)

    @staticmethod
    def _project_row(data, identifier):
        row = next((entry for entry in data["projects"] if entry["id"] == identifier), None)
        if row is None:
            raise LibraryError("storage-project")
        return row

    def _registered_path(self, root, row):
        workspace = _workspace_path(root, row)
        if _project_markers(workspace) != (row["id"], row["database"]):
            raise LibraryError("storage-verification")
        if "slot" in row and workspace_registration(workspace) != (self.anchor, row["slot"]):
            raise LibraryError("storage-verification")
        return workspace

    def resolve_project(self, identifier):
        with self._locked(exclusive=False):
            root, data = self._state()
            return self._registered_path(root, self._project_row(data, uuid_text(identifier)))

    def _managed_row(self, data, workspace, registration):
        anchor, slot = registration
        if anchor != self.anchor:
            raise LibraryError("storage-project")
        row = next((item for item in data["projects"] if item.get("slot") == slot), None)
        if row is None or str(paths.workspace_id(workspace)) != row["id"]:
            raise LibraryError("storage-verification")
        return row

    def resolve_existing(self, location):
        workspace = canonical_path(location)
        registration = workspace_registration(workspace)
        if registration is None:
            return workspace
        with self._locked(exclusive=False):
            root, data = self._state()
            row = self._managed_row(data, workspace, registration)
            return self._registered_path(root, row)

    def describe(self, location):
        path = canonical_path(location)
        registration = workspace_registration(path)
        with self._locked(exclusive=False):
            root, data = self._state()
            if registration is not None:
                row = self._managed_row(data, path, registration)
                self._registered_path(root, row)
                return self._project(root, row)
            row = next((entry for entry in data["projects"] if _workspace_path(root, entry) == path), None)
            return self._project(root, row) if row is not None else None

    @staticmethod
    def _preview(root, target, data, inventory):
        files, directories = inventory
        projects = [row for row in data["projects"] if "slot" in row]
        if any(Path(row["path"]).is_relative_to(target) for row in data["projects"] if "path" in row):
            raise LibraryError("storage-destination")
        return {
            "source": str(root), "destination": str(target), "files": len(files),
            "bytes": sum(record["bytes"] for record in files.values()), "project_count": len(projects),
            "sha256": fingerprint({"source": str(root), "destination": str(target),
                                  "files": files, "directories": directories}),
        }

    def preview(self, destination):
        with self._locked(exclusive=False):
            root, data = self._state()
            target = _destination(root, self.anchor, destination)
            return self._preview(root, target, data, _inventory(root))

    def move(self, destination, sha256):
        with self._locked():
            root, data = self._state()
            target = _destination(root, self.anchor, destination)
            inventory = _inventory(root)
            preview = self._preview(root, target, data, inventory)
            if preview["sha256"] != sha256:
                raise LibraryError("storage-stale")
            try:
                with TemporaryDirectory(dir=target.parent, prefix=".trellin-move-") as directory:
                    staged = Path(directory) / "library"
                    shutil.copytree(root, staged, symlinks=True)
                    if _inventory(staged) != inventory:
                        raise LibraryError("storage-verification")
                    for row in data["projects"]:
                        if "slot" not in row:
                            continue
                        workspace = _workspace_path(staged, row)
                        if workspace_registration(workspace) != (self.anchor, row["slot"]):
                            raise LibraryError("storage-verification")
                        _check_binding(workspace, row)
                    _sync_copy(staged, inventory)
                    if _inventory(root) != inventory:
                        raise LibraryError("storage-stale")
                    # Claim the exact destination without replacing a directory
                    # selected by another app or process after preview.
                    target.mkdir(mode=0o700)
                    reserved = target.lstat()
                    try:
                        staged.rename(target)
                    except OSError:
                        with suppress(OSError):
                            if reserved.st_ino == target.lstat().st_ino:
                                target.rmdir()
                        raise
                    _sync_file(target.parent)
                # The switched location is published last. The validated
                # source copy remains available for recovery.
                for row in data["projects"]:
                    if "slot" in row:
                        self._registered_path(target, row)
                publish_metadata(self.anchor / LOCATION_FILE, {"root": str(target)})
            except OSError:
                raise LibraryError("storage-unavailable") from None
            return {**preview, "root": str(target), "backup": str(root)}
