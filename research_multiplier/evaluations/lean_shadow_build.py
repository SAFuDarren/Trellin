"""Bind paired Lean evaluations to two explicitly selected wheel installations."""

import ast
from base64 import urlsafe_b64encode
from contextlib import contextmanager
import csv
from dataclasses import dataclass
from hashlib import sha256
from importlib.metadata import distributions
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

from research_multiplier.storage.postgres import DatabaseOperationError, Store
from research_multiplier.protocol.serialization import fingerprint
from . import lean_worker


_PACKAGE = "research_multiplier"
_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_NAME = "research-multiplier"
_SIDES = ("baseline", "candidate")
_IMPLEMENTATION_SUFFIXES = {".py", ".lean", ".sql", ".json"}
_SHARE = PurePosixPath("share/research-multiplier/lean")
_LEAN_FILES = {"lakefile.toml", "lake-manifest.json", "lean-toolchain"}
_METADATA = {"METADATA", "WHEEL"}


def file_sha256(path):
    digest = sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def implementation_digest(package):
    files = (path for path in package.rglob("*") if path.is_file() and path.suffix in _IMPLEMENTATION_SUFFIXES)
    return fingerprint({path.relative_to(package).as_posix(): file_sha256(path) for path in sorted(files)})


def _migration_paths(package):
    root = package / "migrations"
    return {path.relative_to(root): path for path in root.rglob("*")
            if path.is_file() and path.suffix in {".py", ".sql"}}


def _migration_chain(package):
    parents = {}
    for script in (package / "migrations" / "versions").glob("*.py"):
        if script.name == "__init__.py":
            continue
        fields = {}
        try:
            syntax = ast.parse(script.read_bytes())
        except SyntaxError:
            raise ValueError("The Lean shadow installation has an invalid migration file.") from None
        for entry in syntax.body:
            if isinstance(entry, ast.Assign) and len(entry.targets) == 1:
                [target] = entry.targets
                if isinstance(target, ast.Name) and target.id in {"revision", "down_revision", "depends_on"}:
                    try:
                        fields[target.id] = ast.literal_eval(entry.value)
                    except ValueError:
                        raise ValueError("Use wheel installations with literal migration revisions.") from None
        revision, parent = fields.get("revision"), fields.get("down_revision")
        if (not isinstance(revision, str) or "down_revision" not in fields
                or parent is not None and not isinstance(parent, str) or parent in parents
                or fields.get("depends_on") is not None):
            raise ValueError("The Lean shadow installation has an unsupported migration history.")
        parents[parent] = revision
    chain = []
    while (revision := parents.get(chain[-1] if chain else None)) is not None and revision not in chain:
        chain.append(revision)
    if not chain or len(chain) != len(parents):
        raise ValueError("The Lean shadow installation needs a complete linear migration history.")
    return chain


def _copy_installation(source, destination):
    installed = [entry for entry in distributions(path=[str(source)]) if entry.metadata["Name"] == _NAME]
    if len(installed) != 1:
        raise ValueError("Select a directory containing one installed Research Multiplier wheel.")
    [distribution] = installed
    record = distribution.read_text("RECORD")
    if not record:
        raise ValueError("The selected Lean shadow wheel has no installed file record.")
    copied = {}
    for row in csv.reader(record.splitlines()):
        if len(row) != 3:
            raise ValueError("A Lean shadow wheel listed an invalid file record.")
        name, expected, _ = row
        relative = PurePosixPath(name)
        parts = relative.parts
        if parts and parts[0] == _PACKAGE and relative.suffix in _IMPLEMENTATION_SUFFIXES:
            if ".." in parts:
                raise ValueError("A Lean shadow wheel listed an invalid package path.")
        elif relative.name in _LEAN_FILES and parts[-4:-1] == _SHARE.parts:
            relative = _SHARE / relative.name
        elif len(parts) == 2 and parts[0].endswith(".dist-info") and relative.name in _METADATA:
            pass
        else:
            continue
        path = source / relative
        if not expected.startswith("sha256=") or not path.is_file() or path.resolve() != path:
            raise ValueError("A Lean shadow wheel file is missing its recorded SHA-256 or uses a symbolic link.")
        content = path.read_bytes()
        digest = sha256(content)
        if relative.as_posix() in copied or "sha256=" + urlsafe_b64encode(digest.digest()).decode().rstrip("=") != expected:
            raise ValueError("The installed Lean shadow wheel does not match its recorded files.")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        copied[relative.as_posix()] = digest.hexdigest()
    required = {f"{_PACKAGE}/__init__.py", *((_SHARE / name).as_posix() for name in _LEAN_FILES)}
    worker_paths = {f"{_PACKAGE}/lean_shadow.py", f"{_PACKAGE}/evaluations/lean_shadow.py"}
    if not required <= copied.keys() or not worker_paths.intersection(copied):
        raise ValueError("Select a complete installed Research Multiplier wheel with Lean shadow support.")
    return distribution.version, fingerprint(copied)


@dataclass(frozen=True)
class Build:
    directory: Path
    implementation_sha256: str
    distribution_version: str
    installation_sha256: str
    migration_head: str

    def identity(self):
        return {"distribution_version": self.distribution_version, "installation_sha256": self.installation_sha256,
                "migration_head": self.migration_head}

    def command(self):
        return lean_worker.command(self.directory, self.implementation_sha256)


@contextmanager
def pair(baseline=None, candidate=None):
    """Snapshot verified wheel files before launching either side."""
    if baseline is None and candidate is None:
        yield {}
        return
    if baseline is None or candidate is None:
        raise ValueError("Select both --baseline-install and --candidate-install for a build comparison.")
    evaluator = _PACKAGE_ROOT
    history = _migration_chain(evaluator)
    known = _migration_paths(evaluator)
    with TemporaryDirectory(prefix="rm-lean-paired-builds-") as directory:
        builds = {}
        for side, location in zip(_SIDES, (baseline, candidate)):
            source = Path(location).expanduser().resolve()
            if not source.is_dir():
                raise ValueError(f"Select the installed Research Multiplier {side} directory.")
            clean = Path(directory).resolve() / side
            version, installation = _copy_installation(source, clean)
            package = clean / _PACKAGE
            chain = _migration_chain(package)
            actual = _migration_paths(package)
            if (chain != history[:len(chain)] or actual.keys() - known.keys()
                    or any(file_sha256(path) != file_sha256(known[name]) for name, path in actual.items())):
                raise ValueError("Each Lean shadow wheel must use a compatible prefix of the evaluator's exact migration history.")
            builds[side] = Build(clean, implementation_digest(package), version, installation, chain[-1])
        if builds["baseline"].implementation_sha256 == builds["candidate"].implementation_sha256:
            raise ValueError("The selected Lean shadow installations have identical implementation files.")
        yield builds


def database_revision(builds):
    if not builds:
        return None
    try:
        with Store() as store:
            [schema] = store.query("verifier", "SELECT rm.lean_shadow_schema_revision() AS revision")
    except DatabaseOperationError as error:
        if error.state != "42883":
            raise
        raise ValueError("Upgrade the evaluation source and restore both comparison workspaces at its schema revision.") from None
    history = _migration_chain(_PACKAGE_ROOT)
    current = schema["revision"]
    if current not in history or any(history.index(build.migration_head) > history.index(current)
                                     for build in builds.values()):
        raise ValueError("The evaluator database schema does not support both selected build migration histories.")
    return current
