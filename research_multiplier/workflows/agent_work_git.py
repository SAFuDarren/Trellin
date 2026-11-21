"""Read explicitly selected files from an immutable repository revision."""

import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess


_COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_REGULAR_FILES = {b"100644", b"100755"}


def validate_code_revision(revision):
    if not isinstance(revision, str) or not _COMMIT.fullmatch(revision):
        raise ValueError("Select an exact Git commit for this work item.")


def validate_code_path(name):
    if not isinstance(name, str) or not name.isprintable() or "\\" in name:
        raise ValueError("Select a canonical repository-relative file path.")
    path = PurePosixPath(name)
    if (
        not path.parts
        or path.is_absolute()
        or path.as_posix() != name
        or any(part.casefold() in {"..", ".git"} for part in path.parts)
    ):
        raise ValueError("Select a canonical repository-relative file path.")
    return path


def isolated_git_environment():
    environment = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}
    environment.update(
        GIT_ALLOW_PROTOCOL="",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_NO_LAZY_FETCH="1",
        GIT_NO_REPLACE_OBJECTS="1",
        GIT_TERMINAL_PROMPT="0",
    )
    return environment


def read_code_inputs(checkout, revision, selected_paths, *, byte_limit):
    """Build a text input snapshot from a repository the caller has already authorized."""
    validate_code_revision(revision)
    if type(byte_limit) is not int or byte_limit < 0:
        raise ValueError("The authorized input size is invalid.")
    try:
        checkout = Path(checkout).resolve(strict=True)
    except (OSError, RuntimeError, TypeError):
        raise ValueError("Select the top-level Git checkout for this work item.") from None
    git_directory = checkout / ".git"
    objects = git_directory / "objects"
    if git_directory.exists() and (
        not git_directory.is_dir() or git_directory.is_symlink()
        or any(directory.is_symlink() for directory in (objects, objects / "info", objects / "pack"))
        or any((objects / "info" / name).exists() or (objects / "info" / name).is_symlink()
               for name in ("alternates", "http-alternates"))
        or (git_directory / "commondir").exists()
    ):
        raise ValueError("Select an independent checkout with its own Git objects.")
    git = shutil.which("git")
    if git is None:
        raise ValueError("Git is required to read the selected code inputs.")
    environment = isolated_git_environment()

    def read_git(*arguments):
        try:
            return subprocess.run(
                [git, "--literal-pathspecs", *arguments],
                cwd=checkout,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=True,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            raise ValueError("Could not read the selected Git revision.") from None

    try:
        top_level = Path(os.fsdecode(read_git("rev-parse", "--show-toplevel").removesuffix(b"\n"))).resolve(strict=True)
        if top_level != checkout:
            raise ValueError("Select the top-level Git checkout for this work item.")
    except (OSError, RuntimeError):
        raise ValueError("Select the top-level Git checkout for this work item.") from None
    if read_git("cat-file", "-t", revision) != b"commit\n":
        raise ValueError("Select an exact Git commit for this work item.")
    files = {}
    for name in selected_paths:
        validate_code_path(name)
        if name in files:
            raise ValueError("Select each repository-relative file once.")
        entry = read_git("ls-tree", "-z", revision, "--", name)
        metadata, separator, actual = entry.partition(b"\t")
        fields = metadata.split()
        if not separator or actual != name.encode("utf-8") + b"\0" or len(fields) != 3:
            raise ValueError("The selected file is absent from the pinned Git revision.")
        mode, kind, digest = fields
        if mode not in _REGULAR_FILES or kind != b"blob":
            raise ValueError("A code input must be a regular Git file.")
        identifier = digest.decode("ascii")
        remaining = byte_limit - int(read_git("cat-file", "-s", identifier))
        if remaining < 0:
            raise ValueError("The selected files exceed the authorized input size.")
        data = read_git("cat-file", "blob", identifier)
        try:
            if b"\x00" in data:
                raise ValueError("A code input must contain UTF-8 text.")
            files[name] = data.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("A code input must contain UTF-8 text.") from None
        byte_limit = remaining
    return {"revision": revision, "files": files}
