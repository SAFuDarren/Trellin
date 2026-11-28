"""Locate the registered desktop library and lease its managed workspaces."""

from contextlib import contextmanager, suppress
import json
import os
from pathlib import Path
import stat
from tempfile import NamedTemporaryFile
from uuid import UUID


MANAGED_FILE = ".trellin-managed.json"
LOCATION_FILE = "location.json"


class LibraryError(ValueError):
    """A fixed error code that can be shown by the desktop launcher."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def uuid_text(value):
    try:
        if value == str(UUID(value)):
            return value
    except (AttributeError, TypeError, ValueError):
        pass
    raise LibraryError("storage-invalid")


def read_metadata(file):
    try:
        info = file.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 4 * 1024 * 1024:
            raise ValueError
        result = json.loads(file.read_bytes())
    except FileNotFoundError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError):
        raise LibraryError("storage-invalid") from None
    if not isinstance(result, dict) or type(result.get("format_version")) is not int or result["format_version"] != 1:
        raise LibraryError("storage-invalid")
    return result


def publish_metadata(file, document):
    temporary = None
    try:
        with NamedTemporaryFile("w", dir=file.parent, prefix=".trellin-", delete=False, encoding="utf-8") as stream:
            temporary = Path(stream.name)
            json.dump({"format_version": 1, **document}, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, file)
        temporary = None
        # The replace is already visible. Some filesystems cannot sync a
        # directory, so reporting that as a failed publication is misleading.
        with suppress(OSError):
            descriptor = os.open(file.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def canonical_path(value):
    try:
        original = Path(value)
        if not original.is_absolute() or ".." in original.parts or "\x00" in str(original):
            raise ValueError
        return original.resolve()
    except (OSError, RuntimeError, TypeError, ValueError):
        raise LibraryError("storage-invalid") from None


def stored_absolute_path(value):
    """Decode a saved path without following links or requiring the target online."""
    if not isinstance(value, str) or "\x00" in value:
        raise LibraryError("storage-invalid")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or str(path) != value:
        raise LibraryError("storage-invalid")
    return path


def application_home(anchor=None):
    if anchor is None:
        anchor = os.environ.get("RM_TRELLIN_HOME") or Path.home() / "Library" / "Application Support" / "Trellin"
    return canonical_path(anchor)


def library_root(anchor, *, required=False):
    try:
        saved = read_metadata(anchor / LOCATION_FILE)
    except FileNotFoundError:
        if required:
            raise LibraryError("storage-unavailable") from None
        return anchor / "Library"
    if set(saved) != {"format_version", "root"}:
        raise LibraryError("storage-invalid")
    root = canonical_path(saved["root"])
    if str(root) != saved["root"]:
        raise LibraryError("storage-invalid")
    return root


_MANAGED_REQUIRED = {"format_version", "anchor", "slot"}
_MANAGED_OPTIONAL = {"name", "default", "source_root"}


def workspace_marker(workspace):
    try:
        marker = read_metadata(Path(workspace) / MANAGED_FILE)
    except FileNotFoundError:
        return None
    keys = set(marker)
    if not (_MANAGED_REQUIRED <= keys <= (_MANAGED_REQUIRED | _MANAGED_OPTIONAL)):
        raise LibraryError("storage-invalid")
    anchor = canonical_path(marker["anchor"])
    if str(anchor) != marker["anchor"]:
        raise LibraryError("storage-invalid")
    slot = uuid_text(marker["slot"])
    name = marker.get("name")
    if name is not None:
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120 or not name.isprintable():
            raise LibraryError("storage-invalid")
        name = name.strip()
    default = marker.get("default", False)
    if type(default) is not bool:
        raise LibraryError("storage-invalid")
    source_root = stored_absolute_path(marker["source_root"]) if "source_root" in marker else None
    if default and source_root is not None:
        raise LibraryError("storage-invalid")
    return {"anchor": anchor, "slot": slot, "name": name, "default": default, "source_root": source_root}


def workspace_registration(workspace):
    marker = workspace_marker(workspace)
    if marker is None:
        return None
    return marker["anchor"], marker["slot"]


@contextmanager
def library_lock(anchor, *, exclusive=False):
    try:
        import fcntl
    except ImportError:
        raise LibraryError("storage-unavailable") from None
    try:
        descriptor = os.open(anchor / ".library.lock", os.O_NOFOLLOW | os.O_CREAT | os.O_RDWR, 0o600)
    except OSError:
        raise LibraryError("storage-unavailable") from None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            raise LibraryError("storage-invalid")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_NB | (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH))
        except BlockingIOError:
            raise LibraryError("storage-busy") from None
        except OSError:
            raise LibraryError("storage-unavailable") from None
        yield
    finally:
        os.close(descriptor)


@contextmanager
def workspace_lease(workspace):
    registration = workspace_registration(workspace) if workspace is not None else None
    if registration is None:
        yield
        return
    anchor, slot = registration
    with library_lock(anchor):
        if workspace != library_root(anchor, required=True) / "Workspaces" / slot:
            raise LibraryError("storage-stale")
        yield
