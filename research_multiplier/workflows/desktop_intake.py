"""Read-only, native pre-project inspection and preview of a selected source."""

from contextlib import contextmanager
import os
import stat

from research_multiplier.storage import paths
from research_multiplier.storage.managed_workspaces import LibraryError, _stored_source_identity, stored_absolute_path
from research_multiplier.workflows import desktop_source as source
from research_multiplier.workflows.desktop_workspace import WorkspaceCreationError, resolve_selected_folder
from research_multiplier.workflows.intake import MAX_MODEL_NOTES_BYTES


def _mode(parent, name):
    try:
        return os.stat(name, dir_fd=parent, follow_symlinks=False).st_mode
    except FileNotFoundError:
        return None


def _has_marker(descriptor):
    mode = _mode(descriptor, paths.WORKSPACE_FILE)
    if mode is None:
        return False
    if not stat.S_ISREG(mode):
        raise LibraryError("workspace-occupied")
    return True


def _workspace(root, descriptor):
    # Reopening does the normal marker-content and database validation. Intake
    # only needs to recognize an occupied location, without reading the marker.
    if _has_marker(descriptor):
        return str(root)
    child = _mode(descriptor, "Trellin")
    if child is not None and stat.S_ISLNK(child):
        raise LibraryError("workspace-occupied")
    if child is not None and stat.S_ISDIR(child):
        with source._directory("Trellin", (), parent=descriptor) as legacy:
            if _has_marker(legacy):
                return str(root / "Trellin")
    return None


@contextmanager
def _selected_root(root, *, expected=None, picked=None):
    try:
        with source._source_directory(root) as descriptor:
            identity = source._identity(descriptor)
            if identity is None:
                raise LibraryError("intake-unavailable")
            if (expected is not None and expected != identity
                    or picked is not None and not os.path.samestat(picked, os.fstat(descriptor))):
                raise LibraryError("intake-changed")
            workspace = _workspace(root, descriptor)
            yield descriptor, identity, workspace
            if _workspace(root, descriptor) != workspace or source.source_status(root, identity) != "available":
                raise LibraryError("intake-changed")
    except OSError:
        raise LibraryError("intake-unavailable") from None


def inspect(location):
    if not isinstance(location, str) or not location.isprintable():
        raise LibraryError("storage-invalid")
    try:
        root, picked = resolve_selected_folder(location)
        with _selected_root(root, picked=picked) as (_, identity, workspace):
            if not os.path.samestat(picked, os.stat(location)):
                raise LibraryError("intake-changed")
            return {"root": str(root), "identity": identity, "workspace": workspace}
    except WorkspaceCreationError as error:
        raise LibraryError(error.args[0]) from None


def preview(location, identity, references):
    if not isinstance(location, str) or not location.isprintable():
        raise LibraryError("storage-invalid")
    root = stored_absolute_path(location)
    identity = _stored_source_identity(identity)
    selected = source.validate_references(references)
    with _selected_root(root, expected=identity) as (descriptor, _, workspace):
        if workspace is not None:
            raise LibraryError("workspace-occupied")
        return source.preview_text_files(descriptor, selected, MAX_MODEL_NOTES_BYTES)
