"""Check explicitly selected files at one local research source."""

from contextlib import contextmanager
from errno import ELOOP, ENOENT, ENOTDIR
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat

from research_multiplier.storage.managed_workspaces import LibraryError


_MAX_FILES = 16
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


def _references(document):
    if not isinstance(document, list) or not 1 <= len(document) <= _MAX_FILES:
        raise LibraryError("storage-invalid")
    seen = set()
    references = []
    for item in document:
        if not isinstance(item, dict) or item.keys() not in ({"path"}, {"path", "sha256"}):
            raise LibraryError("storage-invalid")
        name = item["path"]
        if (not isinstance(name, str) or not name.isprintable() or "\\" in name
                or len(name) > _MAX_PATH_BYTES or len(name.encode("utf-8")) > _MAX_PATH_BYTES):
            raise LibraryError("storage-invalid")
        path = PurePosixPath(name)
        if (not path.parts or path.is_absolute() or path.as_posix() != name or name in seen
                or any(part.casefold() in {"..", ".git"} for part in path.parts)):
            raise LibraryError("storage-invalid")
        previous = item.get("sha256")
        if "sha256" in item and (not isinstance(previous, str) or not _SHA256.fullmatch(previous)):
            raise LibraryError("storage-invalid")
        seen.add(name)
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
            _verify_file(root, path, info)
    except _ObservationError as error:
        return {"path": name, "status": str(error)}
    except OSError as error:
        status = {ENOENT: "missing", ENOTDIR: "unsupported", ELOOP: "unsupported"}.get(error.errno, "inaccessible")
        return {"path": name, "status": status}
    status = "available" if previous is None else ("unchanged" if digest == previous else "changed")
    return {"path": name, "status": status, "sha256": digest}


def _root_matches(root, expected):
    try:
        with _directory(root.anchor, root.parts[1:]) as current:
            return os.path.samestat(expected, os.fstat(current))
    except OSError:
        return False


def check_references(root: Path, references: object) -> dict:
    """Report bounded digest observations without persisting or returning file contents."""
    selected = _references(references)
    if not _DESCRIPTOR_IO or not root.is_absolute() or ".." in root.parts or "\x00" in str(root):
        return {"status": "unavailable", "files": []}
    try:
        with _directory(root.anchor, root.parts[1:]) as descriptor:
            identity = os.fstat(descriptor)
            files = [_observe(descriptor, path, previous) for path, previous in selected]
            if not _root_matches(root, identity):
                return {"status": "unstable", "files": []}
    except OSError:
        return {"status": "unavailable", "files": []}
    return {"status": "available", "files": files}
