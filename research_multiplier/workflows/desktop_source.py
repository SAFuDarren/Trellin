"""Check explicitly selected files at one local research source."""

from contextlib import contextmanager
from errno import ELOOP, ENOENT, ENOTDIR
import hashlib
from math import isfinite
import os
from pathlib import Path, PurePosixPath
import re
import stat

from research_multiplier.storage.managed_workspaces import LibraryError


MAX_FILE_CHECKS = 16
_MAX_FILE_BYTES = 4 * 1024 * 1024
_MAX_PATH_BYTES = 4096
_CHUNK_BYTES = 64 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")
_DESCRIPTOR_IO = (
    os.open in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and os.stat in os.supports_follow_symlinks
    and all(hasattr(os, flag) for flag in ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK"))
)


def validate_reference_path(name: object) -> PurePosixPath:
    if (not isinstance(name, str) or not name.isprintable() or "\\" in name
            or len(name) > _MAX_PATH_BYTES or len(name.encode("utf-8")) > _MAX_PATH_BYTES):
        raise LibraryError("storage-invalid")
    path = PurePosixPath(name)
    if (not path.parts or path.is_absolute() or path.as_posix() != name
            or any(part.casefold() in {"..", ".git"} for part in path.parts)):
        raise LibraryError("storage-invalid")
    return path


def validate_reference_sha256(value: object) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise LibraryError("storage-invalid")
    return value


def _references(document):
    if not isinstance(document, list) or not 1 <= len(document) <= MAX_FILE_CHECKS:
        raise LibraryError("storage-invalid")
    seen = set()
    references = []
    for item in document:
        if not isinstance(item, dict) or item.keys() not in ({"path"}, {"path", "sha256"}):
            raise LibraryError("storage-invalid")
        path = validate_reference_path(item["path"])
        if path in seen:
            raise LibraryError("storage-invalid")
        previous = validate_reference_sha256(item["sha256"]) if "sha256" in item else None
        seen.add(path)
        references.append((path, previous))
    return references


@contextmanager
def _directory(start, parts, *, parent=None):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(start, flags, dir_fd=parent)
    try:
        for part in parts:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def _source_directory(root):
    if not _DESCRIPTOR_IO or not root.is_absolute() or ".." in root.parts or "\x00" in str(root):
        raise OSError("Source root is unavailable.")
    with _directory(root.anchor, root.parts[1:]) as descriptor:
        yield descriptor


def _identity(descriptor):
    info = os.fstat(descriptor)
    if info.st_dev < 0 or info.st_ino <= 0:
        return None
    # Use macOS's st_birthtime, shared by supported CPython versions, so a
    # Python upgrade does not change the representation of the same directory.
    birth = getattr(info, "st_birthtime", None)
    birth_ns = round(birth * 1_000_000_000) if birth is not None and isfinite(birth) and birth >= 0 else None
    return {"device": info.st_dev, "inode": info.st_ino, "birth_time_ns": birth_ns}


def source_identity(root: Path) -> dict | None:
    """Capture a local directory identity without following its path's symlinks."""
    try:
        with _source_directory(root) as descriptor:
            return _identity(descriptor)
    except OSError:
        return None


def _descriptor_status(descriptor, expected):
    current = _identity(descriptor)
    if current is None:
        return "unavailable"
    if expected is None:
        return "needs_confirmation"
    return "available" if current == expected else "identity_changed"


def source_status(root: Path, expected_identity: dict | None) -> str:
    """Report whether the current directory matches the approved local identity."""
    try:
        with _source_directory(root) as descriptor:
            return _descriptor_status(descriptor, expected_identity)
    except OSError:
        return "unavailable"


def _version(info):
    return info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns


class _ObservationError(Exception):
    pass


@contextmanager
def _regular_file(root, path):
    with _directory(".", path.parts[:-1], parent=root) as parent:
        expected = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(expected.st_mode):
            raise _ObservationError("unsupported")
        try:
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        except OSError as error:
            if error.errno in {ENOENT, ENOTDIR, ELOOP}:
                raise _ObservationError("unstable") from None
            raise
        try:
            info = os.fstat(descriptor)
            if _version(info) != _version(expected):
                raise _ObservationError("unstable")
            yield descriptor, info
        finally:
            os.close(descriptor)


def _hash_file(descriptor, size):
    digest = hashlib.sha256()
    while size:
        chunk = os.read(descriptor, min(size, _CHUNK_BYTES))
        if not chunk:
            raise _ObservationError("unstable")
        digest.update(chunk)
        size -= len(chunk)
    return digest.hexdigest()


def _verify_file(root, path, expected):
    # Reopen the name from the root. Editors can replace a regular file or
    # parent without changing the original descriptor.
    try:
        with _regular_file(root, path) as (_, current):
            if _version(current) == _version(expected):
                return
    except (OSError, _ObservationError):
        pass
    raise _ObservationError("unstable")


def _observe(root, path, previous):
    name = path.as_posix()
    try:
        with _regular_file(root, path) as (descriptor, info):
            if info.st_size > _MAX_FILE_BYTES:
                raise _ObservationError("too_large")
            digest = _hash_file(descriptor, info.st_size)
            if _version(info) != _version(os.fstat(descriptor)):
                raise _ObservationError("unstable")
    except _ObservationError as error:
        return {"path": name, "status": str(error)}, None
    except OSError as error:
        status = {ENOENT: "missing", ENOTDIR: "unsupported", ELOOP: "unsupported"}.get(error.errno, "inaccessible")
        return {"path": name, "status": status}, None
    status = "available" if previous is None else ("unchanged" if digest == previous else "changed")
    return {"path": name, "status": status, "sha256": digest}, info


def _settled_observation(root, path, observation):
    report, info = observation
    try:
        if info is not None:
            _verify_file(root, path, info)
        elif report["status"] == "missing":
            with _regular_file(root, path):
                raise _ObservationError("unstable")
    except FileNotFoundError:
        return report
    except (OSError, _ObservationError):
        return {"path": report["path"], "status": "unstable"}
    return report


def check_references(root: Path, references: object, *, expected_identity: dict | None) -> dict:
    """Report bounded digest observations without persisting or returning file contents."""
    selected = _references(references)
    try:
        with _source_directory(root) as descriptor:
            status = _descriptor_status(descriptor, expected_identity)
            if status != "available":
                return {"status": status, "files": []}
            observations = [_observe(descriptor, path, previous) for path, previous in selected]
            # A later observation can overlap with an editor finishing an
            # earlier save. Recheck the names without hashing again.
            files = [_settled_observation(descriptor, path, observation)
                     for (path, _), observation in zip(selected, observations, strict=True)]
            if source_status(root, expected_identity) != "available":
                return {"status": "unstable", "files": []}
    except OSError:
        return {"status": "unavailable", "files": []}
    return {"status": "available", "files": files}
