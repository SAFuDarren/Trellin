"""Watch the compiled inputs of named, already reviewed Lake modules."""

from importlib.resources import files
import json
import os
from pathlib import Path
from time import monotonic
from uuid import UUID

from .. import general_lean, lean_mathlib
from research_multiplier.protocol.serialization import fingerprint


def _stat(path):
    try:
        state = path.stat()
    except FileNotFoundError:
        return None
    return [state.st_dev, state.st_ino, state.st_size, state.st_mtime_ns, state.st_ctime_ns]


def _stamp(paths):
    return fingerprint([(str(path), _stat(path)) for path in sorted(paths)])


class LakeWatch:
    """Cache discovery per distinct import closure. File stats only schedule work."""

    def __init__(self, default=None, mapping=None, *, lean=None):
        default = default or os.environ.get("RM_LEAN_MATHLIB_PROJECT")
        self.default = Path(default).expanduser().resolve() if default else None
        self.projects = self._read_mapping(mapping) if mapping else {}
        self.lean = lean
        self.closures = {}
        self.recorded = {}
        self.active_projects = {}

    @staticmethod
    def _read_mapping(path):
        source = Path(path).expanduser().resolve(strict=True)
        if source.stat().st_size > 262_144:
            raise ValueError("The Lake project mapping exceeds the size limit.")
        document = json.loads(source.read_bytes())
        if not isinstance(document, dict) or len(document) > 128:
            raise ValueError("Provide a JSON object mapping up to 128 Lean statement IDs to Lake project folders.")
        result = {}
        for key, folder in document.items():
            if not isinstance(folder, str) or not folder.strip():
                raise ValueError("Each Lake project path must be nonempty.")
            resolved = (source.parent / Path(folder).expanduser()).resolve()
            result[UUID(key)] = resolved
        return result

    def _candidate(self, statement):
        return self.projects.get(UUID(str(statement)), self.default)

    def _files_and_stamp(self, project, modules):
        key = (project, tuple(modules))
        previous, observed = self.closures.get(key, (None, None))
        if previous is not None:
            current = _stamp(previous)
            if current == observed:
                return current
        roots = lean_mathlib._build_roots(project)
        base = {project / label for label in ("lean-toolchain", "lake-manifest.json", "lakefile.toml", "lakefile.lean")}
        base.update((project / ".lake" / "build" / "lib" / "lean", project / ".lake" / "packages"))
        base.update(roots)
        base.update(root / Path(*name.split(".")).with_suffix(".olean") for root in roots for name in modules)
        try:
            executable = general_lean._executable(self.lean, project)
            library = (executable.parent.parent / "lib" / "lean").resolve(strict=True)
            base.add(executable)
            base.update(library.glob("lib*shared*"))
            helper = files("research_multiplier").joinpath("lean_support", "ImportClosure.lean").read_bytes()
            closure = lean_mathlib._closure(executable, modules, [library, *roots], helper, monotonic() + 30)
        except (OSError, ValueError):
            # A missing or incomplete build will be tried when its files appear.
            watched = base | (previous or set())
        else:
            watched = base | set(closure.values())
        stamp = _stamp(watched)
        self.closures[key] = (watched, stamp)
        return stamp

    def project_for(self, statement):
        return self.active_projects.get(statement)

    def observe(self, store, scope):
        self.active_projects.clear()
        if not (self.default or self.projects):
            return
        targets = store.query("scheduler", "SELECT * FROM rm.lean_progress_watch_targets(%s::uuid[])", scope)
        active = set()
        current = {}
        for target in targets:
            project = self._candidate(target["statement_id"])
            if project is None:
                continue
            self.active_projects[target["statement_id"]] = project
            identifier = target["id"]
            active.add(identifier)
            key = (project, tuple(target["modules"]))
            if key not in current:
                current[key] = self._files_and_stamp(project, target["modules"])
            stamp = current[key]
            if self.recorded.get(identifier) != stamp:
                store.query("scheduler", "SELECT rm.record_lean_progress_runtime_signal(%s,%s)", identifier, stamp)
                self.recorded[identifier] = stamp
        self.recorded = {identifier: stamp for identifier, stamp in self.recorded.items() if identifier in active}
        self.closures = {key: snapshot for key, snapshot in self.closures.items() if key in current}

    def reset_observations(self):
        self.recorded.clear()
