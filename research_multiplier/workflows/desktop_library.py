"""Provision, register and relocate Trellin's user-owned library on a local Mac."""

from contextlib import contextmanager, suppress
import os
from pathlib import Path
import re
import shutil
import stat
from tempfile import TemporaryDirectory
from uuid import uuid4

from research_multiplier.protocol.serialization import fingerprint
from research_multiplier.storage import credentials, paths
from research_multiplier.storage.managed_workspaces import (
    LibraryError, LOCATION_FILE, MANAGED_FILE, application_home, canonical_path, library_lock, library_root,
    publish_metadata, read_metadata, stored_absolute_path, uuid_text, workspace_marker, workspace_registration,
)
from research_multiplier.storage.project_archive import _fingerprint
from research_multiplier.workflows.desktop_workspace import WorkspaceCreationError, create_workspace, inspect_folder


_REGISTRY = "registry.json"
# Managed projects removed from the list keep their folder; this records their name so Open Folder restores it.
_REMOVED = "removed.json"
_SETTINGS = "settings.json"
DEFAULT_NAME = "My research"
# Earlier releases named the default project after the library itself (#302).
_LEGACY_DEFAULT_NAME = "Library"
_ROOT_INFO = {"format_version", "default_id", "default_slot", "projects"}
_NODE_ID = re.compile(r"^[a-z-]+:[0-9a-f:-]+$")


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


def _separate_source(source, data_root):
    if (any(_same_source(source, parent) for parent in (data_root, *data_root.parents))
            or any(_same_source(data_root, parent) for parent in (source, *source.parents))):
        raise LibraryError("storage-destination")


def _same_source(source, saved):
    if source == saved:
        return True
    if str(source).casefold() == str(saved).casefold():
        # Path.resolve can preserve spelling on a case-insensitive volume.
        with suppress(OSError):
            return source.samefile(saved)
    return False


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


def _legacy_default(data):
    default = data["default_id"]
    return default is not None and any(
        row["id"] == default and row["name"] == _LEGACY_DEFAULT_NAME for row in data["projects"]
    )


def _has_claims(row):
    """Ask the project's database whether it records any claim. Unknown answers count as yes."""
    try:
        import psycopg

        logins = credentials.read(row["id"])
        with psycopg.connect(logins["RM_HUMAN_DSN"], connect_timeout=5,
                             options="-c statement_timeout=5000 -c default_transaction_read_only=on") as connection:
            # Any claim counts, archived ones included: a project with history is never hidden.
            return connection.execute("SELECT EXISTS (SELECT 1 FROM rm.claims)").fetchone()[0] is not False
    except Exception:
        # Driver diagnostics can contain credentials. Never send them to the UI.
        return True


def _is_empty(root, row):
    """A project with no inputs and no claims. Anything unreadable counts as not empty, so it stays listed."""
    inputs = _workspace_path(root, row) / "inputs"
    try:
        if inputs.exists() and any(not entry.name.startswith(".") for entry in inputs.iterdir()):
            return False
    except OSError:
        return False
    return not _has_claims(row)


def _position(value):
    if not isinstance(value, dict) or set(value) != {"claimId", "nodeId"}:
        raise LibraryError("storage-invalid")
    claim_id = uuid_text(value["claimId"])
    node_id = value["nodeId"]
    if not isinstance(node_id, str) or len(node_id.encode("utf-8")) > 100 or _NODE_ID.fullmatch(node_id) is None:
        raise LibraryError("storage-invalid")
    return {"claimId": claim_id, "nodeId": node_id}


def _read_settings(root):
    """Saved preferences are a hint for what to open, never a condition for opening (#285)."""
    empty = {"last_project_id": None, "positions": {}}
    try:
        raw = read_metadata(root / _SETTINGS)
    except (FileNotFoundError, LibraryError):
        return empty
    if not (set(raw) <= {"format_version", "last_project_id", "positions"}):
        return empty
    last_id = None
    if raw.get("last_project_id") is not None:
        with suppress(LibraryError):
            last_id = uuid_text(raw["last_project_id"])
    positions = {}
    raw_positions = raw.get("positions")
    if isinstance(raw_positions, dict):
        for key, value in raw_positions.items():
            with suppress(LibraryError):
                positions[uuid_text(key)] = _position(value)
    return {"last_project_id": last_id, "positions": positions}


def _write_settings(root, data):
    publish_metadata(root / _SETTINGS, {"last_project_id": data["last_project_id"], "positions": data["positions"]})


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

    def _write_managed_marker(self, workspace, slot, name, *, default=False, source_root=None):
        document = {"anchor": str(self.anchor), "slot": slot, "name": name, "default": bool(default)}
        if source_root is None and (existing := workspace_marker(workspace)) is not None:
            source_root = existing["source_root"]
        if source_root is not None:
            document["source_root"] = str(source_root)
        publish_metadata(workspace / MANAGED_FILE, document)

    def _managed_markers(self, root):
        for workspace in (root / "Workspaces").iterdir():
            if not stat.S_ISDIR(workspace.lstat().st_mode):
                continue
            marker = workspace_marker(workspace)
            if marker is not None:
                if marker["anchor"] != self.anchor or marker["slot"] != workspace.name:
                    raise LibraryError("storage-invalid")
                yield workspace, marker

    def _rebuild_registry(self, root):
        """Rebuild a missing registry from managed workspaces' .trellin-managed.json markers (#285)."""
        _directory(root)
        projects_dir = root / "Workspaces"
        _directory(projects_dir)
        removed = self._removed(root)
        discovered = []
        seen_ids = set()
        for entry, marker in self._managed_markers(root):
            if marker["slot"] in removed:
                continue
            identifier, database = _project_markers(entry)
            if identifier in seen_ids:
                raise LibraryError("storage-invalid")
            seen_ids.add(identifier)
            discovered.append((entry.lstat().st_mtime_ns, marker["slot"], marker, identifier, database))

        default_item = None
        default_candidates = [item for item in discovered if item[2]["source_root"] is None]
        if default_candidates:
            flagged = [item for item in default_candidates if item[2]["default"]]
            if flagged:
                named = [item for item in flagged if item[2]["name"] in (DEFAULT_NAME, _LEGACY_DEFAULT_NAME)]
                default_item = min(named or flagged, key=lambda item: (item[0], item[1]))
            else:
                named = [item for item in default_candidates if item[2]["name"] in (DEFAULT_NAME, _LEGACY_DEFAULT_NAME)]
                default_item = named[0] if len(named) == 1 else min(default_candidates, key=lambda item: (item[0], item[1]))

        ordered = sorted(discovered, key=lambda item: (item is not default_item, item[0], item[1]))
        projects = []
        for _, slot, marker, identifier, database in ordered:
            is_default = default_item is not None and slot == default_item[1]
            name = marker["name"]
            if is_default:
                name = DEFAULT_NAME if name in (None, _LEGACY_DEFAULT_NAME) else name
            elif name is None:
                name = f"Project {slot[:8]}"
            projects.append({"id": identifier, "database": database, "name": _name(name), "slot": slot})

        document = {
            "default_id": default_item[3] if default_item is not None else None,
            "default_slot": default_item[1] if default_item is not None else str(uuid4()),
            "projects": projects,
        }
        publish_metadata(root / _REGISTRY, document)
        return self._read(root)

    def _state(self, *, initialize=False, repair=False):
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
                # An interrupted atomic initial write can leave its temporary
                # file. Leave it intact while rebuilding the empty registry.
                if any(projects.iterdir()) or any(
                    entry != projects and (not entry.name.startswith(".trellin-")
                                           or not stat.S_ISREG(entry.lstat().st_mode))
                    for entry in root.iterdir()
                ):
                    raise LibraryError("storage-invalid")
                publish_metadata(root / _REGISTRY, {"default_id": None, "default_slot": str(uuid4()), "projects": []})
            registry = self._read(root)
            if registry["projects"] or any(projects.iterdir()):
                raise LibraryError("storage-invalid")
            publish_metadata(self.anchor / LOCATION_FILE, {"root": str(root)})
            return root, registry
        if repair and not (root / _REGISTRY).exists():
            return root, self._rebuild_registry(root)
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
                path = stored_absolute_path(row["path"])
                if path.is_relative_to(root) or path in external:
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
        with self._locked(exclusive=False):
            if (self.anchor / LOCATION_FILE).exists():
                root = library_root(self.anchor, required=True)
                if (root / _REGISTRY).exists():
                    data = self._read(root)
                    if not _legacy_default(data):
                        return self._status(root, data)
        with self._locked():
            root, data = self._state(initialize=True, repair=True)
            if _legacy_default(data):
                row = self._project_row(data, data["default_id"])
                row["name"] = DEFAULT_NAME
                if "slot" in row:
                    self._write_managed_marker(_workspace_path(root, row), row["slot"], DEFAULT_NAME, default=True)
                publish_metadata(root / _REGISTRY, data)
            return self._status(root, data)

    def ensure_ready(self):
        """Create the default library if missing and repair a deleted registry before restore (#285)."""
        return self.status()

    def settings(self):
        """Read the library's remembered project ID and saved node positions (#285)."""
        with self._locked(exclusive=False):
            if not (self.anchor / LOCATION_FILE).exists():
                return {"last_project_id": None, "positions": {}}
            root = library_root(self.anchor, required=True)
            if (root / _REGISTRY).exists():
                self._read(root)
                return _read_settings(root)
        with self._locked():
            root, _ = self._state(repair=True)
            return _read_settings(root)

    def remember_project(self, identifier):
        """Record the active project ID in the library's own settings and return its saved position."""
        project_id = uuid_text(identifier)
        with self._locked():
            root, data = self._state(repair=True)
            self._project_row(data, project_id)
            saved = _read_settings(root)
            if saved["last_project_id"] != project_id:
                saved["last_project_id"] = project_id
                _write_settings(root, saved)
            return saved["positions"].get(project_id)

    def save_position(self, identifier, claim_id, node_id):
        """Save a project's selected graph node in the library's own settings."""
        project_id = uuid_text(identifier)
        position = _position({"claimId": claim_id, "nodeId": node_id})
        with self._locked():
            root, data = self._state(repair=True)
            self._project_row(data, project_id)
            saved = _read_settings(root)
            if saved["positions"].get(project_id) != position:
                saved["positions"][project_id] = position
                _write_settings(root, saved)
            return position

    def import_legacy_positions(self, positions):
        """Migrate valid UUID-keyed legacy positions into library settings without overwriting existing ones."""
        if not isinstance(positions, dict) or not positions:
            return
        with self._locked():
            root, _ = self._state(initialize=True, repair=True)
            saved = _read_settings(root)
            changed = False
            for key, value in positions.items():
                try:
                    project_id = uuid_text(key)
                    position = _position(value)
                except LibraryError:
                    continue
                if project_id not in saved["positions"]:
                    saved["positions"][project_id] = position
                    changed = True
            if changed:
                _write_settings(root, saved)

    def _status(self, root, data):
        projects = [self._project(root, row) | {"default": row["id"] == data["default_id"]}
                    for row in data["projects"]]
        for project, row in zip(projects, data["projects"]):
            # The empty default stays the fallback but leaves the switcher once the researcher has a project.
            project["hidden"] = project["default"] and len(projects) > 1 and _is_empty(root, row)
        return {"root": str(root), "default_id": data["default_id"], "projects": projects}

    def rename(self, identifier, name):
        name = _name(name)
        with self._locked():
            root, data = self._state(repair=True)
            row = self._project_row(data, uuid_text(identifier))
            row["name"] = name
            if "slot" in row:
                self._write_managed_marker(
                    _workspace_path(root, row), row["slot"], name, default=row["id"] == data["default_id"],
                )
            publish_metadata(root / _REGISTRY, data)
            return self._project(root, row)

    def unregister(self, identifier):
        """Remove a project from the list. Its folder and database stay where they are."""
        with self._locked():
            root, data = self._state(repair=True)
            row = self._project_row(data, uuid_text(identifier))
            if row["id"] == data["default_id"]:
                raise LibraryError("storage-project")
            if "slot" in row:
                removed = self._removed(root)
                removed[row["slot"]] = {"id": row["id"], "name": row["name"]}
                publish_metadata(root / _REMOVED, {"projects": removed})
            data["projects"].remove(row)
            publish_metadata(root / _REGISTRY, data)
            saved = _read_settings(root)
            if saved["last_project_id"] == row["id"]:
                saved["last_project_id"] = None
                _write_settings(root, saved)
            return self._project(root, row)

    @staticmethod
    def _removed(root):
        try:
            data = read_metadata(root / _REMOVED)
        except FileNotFoundError:
            return {}
        projects = data.get("projects") if set(data) == {"format_version", "projects"} else None
        if not isinstance(projects, dict) or any(
            not isinstance(entry, dict) or set(entry) != {"id", "name"} for entry in projects.values()
        ):
            raise LibraryError("storage-invalid")
        for slot, entry in projects.items():
            uuid_text(slot)
            uuid_text(entry["id"])
            _name(entry["name"])
        return dict(projects)

    def _create(self, root, data, name, slot, *, default=False, source_root=None):
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
        self._write_managed_marker(workspace, slot, name, default=default, source_root=source_root)
        data["projects"].append({"id": identifier, "database": database, "name": name, "slot": slot})
        if default:
            data["default_id"] = identifier
        publish_metadata(root / _REGISTRY, data)
        return workspace

    def open_default(self):
        with self._locked():
            root, data = self._state(initialize=True, repair=True)
            if data["default_id"] is not None:
                return self._registered_path(root, self._project_row(data, data["default_id"]))
            self._recover_pending_default(root, data)
            return self._create(root, data, DEFAULT_NAME, data["default_slot"], default=True)

    def _recover_pending_default(self, root, data):
        workspace = root / "Workspaces" / data["default_slot"]
        if not workspace.exists() and not workspace.is_symlink():
            return
        _directory(workspace)
        if workspace_registration(workspace) is not None:
            # Publication of this marker follows successful provisioning.
            return
        identifier = paths.workspace_id(workspace)
        if identifier is not None and paths.database_id(workspace) is not None:
            try:
                credentials.location(identifier).lstat()
            except FileNotFoundError:
                pass
            else:
                # The complete candidate must retain its slot if PostgreSQL
                # validation fails transiently on this launch.
                return
        # An interrupted or failed provisioning left an incomplete folder.
        # Preserve its contents and reserve an unused slot for the new attempt.
        data["default_slot"] = str(uuid4())
        publish_metadata(root / _REGISTRY, data)

    def create(self, name):
        name = _name(name)
        with self._locked():
            root, data = self._state(initialize=True, repair=True)
            return self._create(root, data, name, str(uuid4()))

    def open_folder(self, location):
        """Open a known workspace or bind an ordinary directory to an app-owned one."""
        source, existing = inspect_folder(location)
        if existing is not None:
            return self.resolve_existing(existing)
        _separate_source(source, self.anchor)
        with self._locked():
            _separate_source(source, library_root(self.anchor))
            root, data = self._state(initialize=True, repair=True)
            matches = [(workspace, marker) for workspace, marker in self._managed_markers(root)
                       if marker["source_root"] is not None and _same_source(source, marker["source_root"])]
            if len(matches) > 1:
                raise LibraryError("storage-invalid")
            if matches:
                workspace, marker = matches[0]
                registration = marker["anchor"], marker["slot"]
                self._restore_managed(root, data, workspace, registration)
                return self._registered_path(root, self._managed_row(data, workspace, registration))
            try:
                name = _name(source.name)
            except LibraryError:
                name = "Research project"
            return self._create(root, data, name, str(uuid4()), source_root=source)

    def register_external(self, path):
        workspace = canonical_path(path)
        if workspace_registration(workspace) is not None:
            return
        identifier, database = _project_markers(workspace)
        with self._locked():
            root, data = self._state(initialize=True, repair=True)
            if workspace.is_relative_to(root):
                raise LibraryError("storage-project")
            known = next((row for row in data["projects"] if row["id"] == identifier), None)
            if known is not None:
                if (canonical_path(_workspace_path(root, known)) != workspace
                        or known["database"] != database):
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
        if "path" in row:
            workspace = canonical_path(workspace)
            if workspace.is_relative_to(root):
                raise LibraryError("storage-verification")
        if _project_markers(workspace) != (row["id"], row["database"]):
            raise LibraryError("storage-verification")
        if "slot" in row and workspace_registration(workspace) != (self.anchor, row["slot"]):
            raise LibraryError("storage-verification")
        return workspace

    def _resolve_in_state(self, root, data, identifier, *, verify_database=False):
        row = self._project_row(data, uuid_text(identifier))
        workspace = self._registered_path(root, row)
        if verify_database:
            _check_binding(workspace, row)
        return workspace

    def resolve_project(self, identifier, *, verify_database=False):
        with self._locked(exclusive=False):
            root = library_root(self.anchor, required=True)
            if (root / _REGISTRY).exists():
                return self._resolve_in_state(root, self._read(root), identifier, verify_database=verify_database)
        with self._locked():
            root, data = self._state(repair=True)
            return self._resolve_in_state(root, data, identifier, verify_database=verify_database)

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
        with self._locked():
            root, data = self._state(repair=True)
            self._restore_managed(root, data, workspace, registration)
            row = self._managed_row(data, workspace, registration)
            return self._registered_path(root, row)

    def _restore_managed(self, root, data, workspace, registration):
        """Restore a removed project or finish publishing an already provisioned linked folder."""
        anchor, slot = registration
        if anchor != self.anchor or any(row.get("slot") == slot for row in data["projects"]):
            return
        removed = self._removed(root)
        entry = removed.get(slot)
        if workspace != root / "Workspaces" / slot:
            return
        if entry is None:
            marker = workspace_marker(workspace)
            if marker is None or marker["source_root"] is None:
                return
        identifier, database = _project_markers(workspace)
        if (entry is not None and identifier != entry["id"]) or any(row["id"] == identifier for row in data["projects"]):
            raise LibraryError("storage-verification")
        if entry is not None:
            name = entry["name"]
            self._write_managed_marker(workspace, slot, name)
        else:
            name = _name(marker["name"])
            _check_binding(workspace, {"id": identifier, "database": database})
        data["projects"].append({"id": identifier, "database": database, "name": name, "slot": slot})
        publish_metadata(root / _REGISTRY, data)
        if entry is not None:
            del removed[slot]
            publish_metadata(root / _REMOVED, {"projects": removed})

    def _describe_in_state(self, root, data, path, registration):
        if registration is not None:
            row = self._managed_row(data, path, registration)
            self._registered_path(root, row)
            return self._project(root, row)
        row = next((entry for entry in data["projects"] if _workspace_path(root, entry) == path), None)
        return self._project(root, row) if row is not None else None

    def describe(self, location):
        path = canonical_path(location)
        registration = workspace_registration(path)
        with self._locked(exclusive=False):
            root = library_root(self.anchor, required=True)
            if (root / _REGISTRY).exists():
                return self._describe_in_state(root, self._read(root), path, registration)
        with self._locked():
            root, data = self._state(repair=True)
            return self._describe_in_state(root, data, path, registration)

    def _preview(self, root, target, data, inventory):
        files, directories = inventory
        projects = [row for row in data["projects"] if "slot" in row]
        if any(Path(row["path"]).is_relative_to(target) for row in data["projects"] if "path" in row):
            raise LibraryError("storage-destination")
        for _, marker in self._managed_markers(root):
            if marker["source_root"] is not None:
                _separate_source(marker["source_root"], target)
        return {
            "source": str(root), "destination": str(target), "files": len(files),
            "bytes": sum(record["bytes"] for record in files.values()), "project_count": len(projects),
            "sha256": fingerprint({"source": str(root), "destination": str(target),
                                  "files": files, "directories": directories}),
        }

    def preview(self, destination):
        with self._locked(exclusive=False):
            root = library_root(self.anchor, required=True)
            if (root / _REGISTRY).exists():
                data = self._read(root)
                target = _destination(root, self.anchor, destination)
                return self._preview(root, target, data, _inventory(root))
        with self._locked():
            root, data = self._state(repair=True)
            target = _destination(root, self.anchor, destination)
            return self._preview(root, target, data, _inventory(root))

    def move(self, destination, sha256):
        with self._locked():
            root, data = self._state(repair=True)
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
