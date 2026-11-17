"""Measure the executable and dependencies used by standalone Lean Core."""

import ctypes
from hashlib import sha256
from importlib.resources import files
import os
from pathlib import Path
import platform
import re
import subprocess
from tempfile import TemporaryDirectory
from time import monotonic

from . import lean_mathlib
from .serialization import fingerprint


POLICY = "native-lean-v1"
_DARWIN_IMAGE = re.compile(r"^dyld(?:\[\d+\])?: (?:loaded: )?(?:<[\dA-Fa-f-]+> )?(/.+)$")


def _dyld_cache_uuid():
    try:
        query = ctypes.CDLL(None)._dyld_get_shared_cache_uuid
    except AttributeError as error:
        raise ValueError("This macOS loader cannot identify its shared cache.") from error
    query.argtypes = [ctypes.POINTER(ctypes.c_ubyte)]
    query.restype = ctypes.c_bool
    value = (ctypes.c_ubyte * 16)()
    if not query(value):
        raise ValueError("This macOS loader did not identify its shared cache.")
    return bytes(value).hex()


def _run(executable, work, env, args, deadline):
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise ValueError("Lean Core verification exceeded its time budget.")
    try:
        return subprocess.run([str(executable), *args], cwd=work, env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=remaining, check=False)
    except subprocess.TimeoutExpired as error:
        raise ValueError("Lean Core verification exceeded its time budget.") from error


def _loaded_paths(executable, work, env, deadline):
    if platform.system() == "Darwin":
        trace = _run(executable, work, {**env, "DYLD_PRINT_LIBRARIES": "1"}, ["--version"], deadline)
        paths = {match[1] for line in trace.stderr.splitlines() if (match := _DARWIN_IMAGE.fullmatch(line))}
    elif platform.system() == "Linux":
        trace = _run(executable, work, {**env, "LD_TRACE_LOADED_OBJECTS": "1"}, ["--version"], deadline)
        lines = [line.strip().split(" => ", 1)[-1] for line in trace.stdout.splitlines()]
        if any(line == "not found" for line in lines):
            raise ValueError("The Lean runtime has a missing native shared library.")
        paths = {line.split(" (0x", 1)[0] for line in lines if line.startswith("/")}
    else:
        raise ValueError("Standalone Lean Core runtime measurement requires macOS or Linux with glibc.")
    paths.discard(str(executable))
    if trace.returncode or not paths:
        raise ValueError("The native loader did not identify Lean's shared libraries.")
    return sorted(map(Path, paths))


class NativeLean:
    """Pin the closed Lean import graph and loaded native images for this executable."""

    def __init__(self, executable):
        self.executable = Path(executable).resolve(strict=True)
        self.sysroot = self.executable.parent.parent
        self.core = (self.sysroot / "lib" / "lean").resolve(strict=True)
        self.helper = files("research_multiplier").joinpath("lean_support", "ImportClosure.lean").read_bytes()
        deadline = monotonic() + 30
        with TemporaryDirectory(prefix="rm-lean-runtime-") as directory:
            work = Path(directory)
            self.native = self._native_artifacts(work, deadline)
        closure = lean_mathlib._closure(self.executable, (), [self.core], self.helper, deadline)
        self.modules = {relative: lean_mathlib._sha256(path) for relative, path in sorted(closure.items())}
        self.digest = fingerprint({
            "policy": POLICY, "platform": [platform.system(), platform.machine()],
            "loader": self.native, "import_reader": sha256(self.helper).hexdigest(),
            "runtime_driver": lean_mathlib._sha256(Path(__file__)),
            "runtime_support": lean_mathlib._sha256(Path(lean_mathlib.__file__)),
            "compiled_imports": {relative.as_posix(): digest for relative, digest in self.modules.items()},
        })

    def _native_artifacts(self, work, deadline):
        env = lean_mathlib._environment(self.executable, work, work)
        version = _run(self.executable, work, env, ["--version"], deadline)
        if version.returncode or "version 4.19.0," not in version.stdout or "commit 6caaee842e94," not in version.stdout:
            raise ValueError("Use the pinned Lean 4.19.0 release.")
        if platform.system() == "Darwin" and f", {platform.machine()}-apple-darwin" not in version.stdout:
            raise ValueError("Use a Lean executable for the native macOS architecture.")
        disk = {}
        darwin = platform.system() == "Darwin"
        for path in _loaded_paths(self.executable, work, env, deadline):
            if path.is_file():
                key = (f"toolchain:{path.relative_to(self.sysroot).as_posix()}"
                       if path.is_relative_to(self.sysroot) else path.as_posix())
                disk[key] = lean_mathlib._sha256(path)
            elif not (darwin and (path.is_relative_to("/usr/lib") or path.is_relative_to("/System/Library"))):
                raise ValueError("A native library loaded by Lean could not be measured.")
        return {
            "compiler": lean_mathlib._sha256(self.executable), "file_images": disk,
            "shared_cache_uuid": _dyld_cache_uuid() if darwin else None,
            "kernel_release": platform.release(),
        }

    def assert_current(self, work, deadline):
        for relative, digest in self.modules.items():
            try:
                path = (self.core / relative).resolve(strict=True)
                matches = path.is_relative_to(self.core) and lean_mathlib._sha256(path) == digest
            except OSError:
                matches = False
            if not matches:
                raise ValueError(f"A pinned Lean toolchain module changed: {relative.as_posix()}.")
        try:
            observed = self._native_artifacts(work, deadline)
        except (OSError, ValueError) as error:
            raise ValueError("The pinned Lean executable or native runtime libraries could not be revalidated.") from error
        if observed != self.native:
            raise ValueError("The pinned Lean executable or native runtime libraries changed.")

    def run(self, sources, commands):
        deadline = monotonic() + 90
        with TemporaryDirectory(prefix="rm-lean-core-") as directory:
            work = Path(directory)
            self.assert_current(work, deadline)
            for name, content in sources.items():
                if Path(name).name != name:
                    raise ValueError("The checker source must use a plain filename.")
                (work / name).write_bytes(content)
            env = lean_mathlib._environment(self.executable, work, work)
            for command in commands:
                result = _run(self.executable, work, env, command, deadline)
                report = (result.stderr + "\n" + result.stdout).strip().replace(directory, "<lean>")
                if result.returncode:
                    return result.returncode, report
            self.assert_current(work, deadline)
            return 0, report
