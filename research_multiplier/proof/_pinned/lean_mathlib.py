"""Check propositions against a trusted, prebuilt native Mathlib project."""

from hashlib import sha256
from importlib.resources import files
import json
import os
from pathlib import Path
import platform
import re
from shutil import copyfile
import subprocess
from tempfile import TemporaryDirectory
from time import monotonic

from .lean_runner import TOOLCHAIN
from .serialization import fingerprint


CHECKSUM = re.compile(r"[a-f0-9]{64}")
MODULE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*")
RESERVED = {"Lean", "Init", "Std", "RMExpected", "RMCandidate", "Audit", "RMTheory"}
POLICY = "native-mathlib-v1"


def validate_modules(modules):
    if (not isinstance(modules, list) or not 1 <= len(modules) <= 12
            or any(not isinstance(name, str) or len(name) > 160 or not MODULE.fullmatch(name)
                   or name.partition(".")[0] in RESERVED for name in modules)
            or len(set(modules)) != len(modules)):
        raise ValueError("List between one and 12 distinct reviewed project modules.")
    return list(modules)


def validate_manifest(value):
    if (not isinstance(value, dict) or set(value) != {"format_version", "project_sha256", "modules"}
            or type(value["format_version"]) is not int or value["format_version"] != 1
            or not isinstance(value["project_sha256"], str) or not CHECKSUM.fullmatch(value["project_sha256"])):
        raise ValueError("A version 1 Mathlib manifest needs the checked project's SHA-256 digest.")
    return {"format_version": 1, "project_sha256": value["project_sha256"],
            "modules": validate_modules(value["modules"])}


def _sha256(path):
    digest = sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _environment(executable, cwd, library=None):
    env = {
        "PATH": os.pathsep.join([str(executable.parent), os.defpath]),
        "HOME": str(cwd),
        "TMPDIR": str(cwd),
        "LEAN_SYSROOT": str(executable.parent.parent),
        "LANG": "C.UTF-8",
    }
    if library is not None:
        env["LEAN_PATH"] = str(library)
    if os.name == "nt" and "SYSTEMROOT" in os.environ:
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    return env


def _run(executable, cwd, env, command, timeout):
    if timeout <= 0:
        raise ValueError("The native Mathlib check exceeded its time limit.")
    try:
        return subprocess.run([str(executable), *command], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                              check=False)
    except subprocess.TimeoutExpired as error:
        raise ValueError("The native Mathlib check exceeded its time limit.") from error


def _build_roots(project):
    build = project / ".lake" / "build" / "lib" / "lean"
    dependencies = sorted((project / ".lake" / "packages").glob("*/.lake/build/lib/lean"))
    return [path.resolve() for path in [build, *dependencies] if path.is_dir()]


def _closure(executable, modules, roots, helper, deadline):
    with TemporaryDirectory(prefix="rm-lean-imports-") as directory:
        root = Path(directory)
        source = root / "ImportClosure.lean"
        source.write_bytes(helper)
        result = _run(executable, root, _environment(executable, root),
                      ["--run", source.name, *modules, "roots:", *map(str, roots)], deadline - monotonic())
    if result.returncode:
        diagnostic = (result.stdout + "\n" + result.stderr).strip()
        raise ValueError(f"Could not discover the prebuilt Lean imports. {diagnostic[-1800:]}")
    raw = json.loads(result.stdout)
    if not isinstance(raw, list) or not raw or any(not isinstance(path, str) for path in raw):
        raise ValueError("Lean did not report its compiled import paths.")
    found = {}
    for item in raw:
        path = Path(item)
        real = path.resolve(strict=True)
        relative = next((path.relative_to(root) for root in roots if path.is_relative_to(root)
                         and real.is_relative_to(root)), None)
        if relative is None or relative.suffix != ".olean" or not real.is_file():
            raise ValueError("A compiled Lean import is outside the pinned library roots.")
        if relative in found and found[relative] != real:
            raise ValueError("The Lean import closure contains an ambiguous compiled module.")
        found[relative] = real
    return found


class LocalMathlib:
    """Load prebuilt project modules without running Lake or opening an inherited environment."""

    def __init__(self, project, modules, *, executable):
        modules = validate_modules(modules)
        self.project = Path(project).expanduser().resolve()
        self.executable = Path(executable).resolve(strict=True)
        toolchain = self.project / "lean-toolchain"
        lock = self.project / "lake-manifest.json"
        if not toolchain.is_file() or not lock.is_file():
            raise ValueError("Use a prepared Lean project with lean-toolchain and lake-manifest.json.")
        if toolchain.read_text(encoding="utf-8").strip() != TOOLCHAIN:
            raise ValueError("The local Mathlib project must pin Lean 4.19.0.")
        roots = _build_roots(self.project)
        if not roots:
            raise ValueError("No prebuilt Lean modules were found. Build the project before checking it.")
        self.core = (self.executable.parent.parent / "lib" / "lean").resolve(strict=True)
        self.runtime_libraries = sorted(path for path in self.core.iterdir()
                                        if path.is_file() and path.name.startswith(("libInit_shared", "libleanshared"))
                                        and path.suffix in {".dylib", ".so", ".dll"})
        self.runtime_digests = {path: _sha256(path) for path in self.runtime_libraries}
        self.compiler_digest = _sha256(self.executable)
        helper = files("research_multiplier").joinpath("lean_support", "ImportClosure.lean").read_bytes()
        closure = _closure(self.executable, modules, [self.core, *roots], helper, monotonic() + 30)
        self.artifacts = {relative: (path, _sha256(path)) for relative, path in sorted(closure.items())}
        self.manifest = validate_manifest({
            "format_version": 1, "modules": modules,
            "project_sha256": fingerprint({
                "platform": [platform.system(), platform.machine()],
                "toolchain": _sha256(toolchain), "lake_manifest": _sha256(lock),
                "compiler": self.compiler_digest, "import_reader": sha256(helper).hexdigest(),
                "compiled_imports": {relative.as_posix(): checksum
                                     for relative, (_, checksum) in self.artifacts.items()},
                "runtime_libraries": {path.name: checksum for path, checksum in self.runtime_digests.items()},
            }),
        })

    def run(self, sources, commands):
        deadline = monotonic() + 90
        with TemporaryDirectory(prefix="rm-lean-mathlib-") as directory:
            work = Path(directory)
            library = work / "imports"
            if _sha256(self.executable) != self.compiler_digest:
                raise ValueError("The pinned Lean compiler changed before verification.")
            for path, checksum in self.runtime_digests.items():
                if _sha256(path) != checksum:
                    raise ValueError("The pinned Lean runtime library changed before verification.")
            for relative, (original, checksum) in self.artifacts.items():
                if original.is_relative_to(self.core):
                    if _sha256(original) != checksum:
                        raise ValueError("A pinned Lean toolchain module changed before verification.")
                    continue
                copied = library / relative
                copied.parent.mkdir(parents=True, exist_ok=True)
                copyfile(original, copied)
                if _sha256(copied) != checksum:
                    raise ValueError("A pinned Mathlib module changed before verification.")
            for name, content in sources.items():
                if Path(name).name != name:
                    raise ValueError("The checker source must use a plain filename.")
                (work / name).write_bytes(content)
            env = _environment(self.executable, work, os.pathsep.join([str(work), str(library)]))
            version = _run(self.executable, work, env, ["--version"], deadline - monotonic())
            if version.returncode or "version 4.19.0," not in version.stdout or "commit 6caaee842e94," not in version.stdout:
                raise ValueError("Use the pinned Lean 4.19.0 release.")
            report = ""
            for command in commands:
                result = _run(self.executable, work, env, command, deadline - monotonic())
                report = (result.stderr + "\n" + result.stdout).strip()
                if result.returncode:
                    return result.returncode, report
            return 0, report
