"""Load the pinned Lean setup or explicitly supplied legacy sources."""

from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path, PurePosixPath
import sysconfig

from research_multiplier.lean_candidates import AUDIT_SHA256, AUDIT_SOURCE
from research_multiplier.proof.lean_runner import PINNED_SOURCES, digest


SETUP_SOURCES = {
    "lean-toolchain": PINNED_SOURCES["lean-toolchain"],
    "lakefile.toml": "1d7890b0f72db5dbf96b916fd68ae463840f665282db34bdb8ad824eac96add7",
    "lake-manifest.json": "07c1813e3aa1c0b81649f668d31c93e5ca63eeb474e79d532ab946514ebe2b94",
}
LEGACY_SOURCES = {**PINNED_SOURCES, AUDIT_SOURCE: AUDIT_SHA256}


def _candidate_roots():
    root = Path(__file__).resolve().parents[2]
    shared = Path("share") / "research-multiplier" / "lean"
    yield root / "lean"
    try:
        installed = distribution("research-multiplier")
    except PackageNotFoundError:
        installed = None
    if installed is not None:
        for path in installed.files or ():
            if PurePosixPath(path.as_posix()).parts[-4:] == (*shared.parts, "lean-toolchain"):
                yield Path(installed.locate_file(path)).parent
    yield root / shared
    yield Path(sysconfig.get_path("data")) / shared


def _pinned_sources(directory, checksums):
    contents = {}
    for name, checksum in checksums.items():
        path = directory / name
        missing = f"Cannot read pinned Lean project input {name} in {directory}."
        try:
            if not path.is_file():
                raise ValueError(missing)
            content = path.read_bytes()
        except OSError as error:
            raise ValueError(missing) from error
        if digest(content) != checksum:
            raise ValueError(f"Lean project input differs from its pinned source: {name}.")
        contents[name] = content
    return contents


def read(source=None):
    """Return one source directory and the bytes checked against the pinned hashes."""
    if source is not None:
        directory = Path(source)
        try:
            contents = _pinned_sources(directory, SETUP_SOURCES)
        except ValueError:
            if not (directory / "RMFlow").is_dir():
                raise
            contents = _pinned_sources(directory, LEGACY_SOURCES)
        return directory.resolve(), contents
    for directory in _candidate_roots():
        try:
            contents = _pinned_sources(directory, SETUP_SOURCES)
        except ValueError:
            continue
        return directory.resolve(), contents
    raise ValueError("The pinned Lean setup is unavailable. Install the release wheel or supply a verified source path.")
