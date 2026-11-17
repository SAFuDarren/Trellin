"""Run the pinned Lean checker against the supplied fixed-network case project."""

import hashlib
import json
import os
from pathlib import Path
from shutil import which
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic

from research_multiplier.protocol.serialization import fingerprint


TOOLCHAIN = "leanprover/lean4:v4.19.0"
EXPECTED_THEOREM = "RMFlow.Expected.realGoal"
NETWORK_SHA256 = "09a0996e8f88bb2f6ea32de2c49e092bd3aa5c1a4d83a5ef09eb6870e3ffe628"
AXIOMS = {"Classical.choice", "propext", "Quot.sound"}

# These are the supported, reviewed project inputs. Candidates are examples,
# not arbitrary Lean programs supplied at a user or agent boundary.
PINNED_SOURCES = {
    "lean-toolchain": "55e97be96000b5e9e290c9e74482e5e317861499a5540353ce845471bded8cea",
    "lakefile.toml": "e7543aec42cb6b7c8437852f6be8308d818c1877d1f80650ef45584be5391388",
    "lake-manifest.json": "b7153d8bd8a9ed319aeacad244c2ec3b84081bd3f6808f8b7580b22c2ee1fcc1",
    "RMFlow/Expected.lean": "2c572b8a355483d808a6e06dc43fd6fe023f8646f56945153e36d40a2fdfa85f",
    "RMFlow/InvalidProof.lean": "b7e9ef6376789ac64bf26d3db79952875b4dec1b96c5eff2f4e062f267a864db",
    "RMFlow/IntegerProof.lean": "9b48c6cda2a7041a6f8ac4de0b4b314d5f69e7da48657dea3fbf08bc91255a33",
    "RMFlow/RealProof.lean": "5877d5e697032c9e4f6f907eecf5ea47f0f84c48f57354cfcb470f9d99541c1c",
    "RMFlow/Certificate.lean": "75c64d86f6107b26262cedd173df878c834a845449dfedefedda97952fca481d",
    "RMFlow/Audit.lean": "7ed26910509e5531b87b1558bc1dc72b36e592791bc20ab908b8203b1bcf97a8",
}

CANDIDATES = {
    "real": (("Certificate", "RealProof"), "RMFlow.RealProof.proof"),
    "integer": (("Certificate", "IntegerProof"), "RMFlow.IntegerProof.proof"),
    "axiom": (("Certificate", "RealProof", "InvalidProof"), "RMFlow.InvalidProof.proof"),
    "helper": (("Certificate", "RealProof", "InvalidProof"), "RMFlow.InvalidProof.resolvedWithoutApprovedDependency"),
    "extra": (("Certificate", "RealProof", "InvalidProof"), "RMFlow.InvalidProof.proofUnderExtraAssumption"),
}
CHECKER = {"name": "rm-lean-flow-audit", "version": "1"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def artifact_ref(data):
    checksum = digest(data)
    return {"uri": f"urn:sha256:{checksum}", "sha256": checksum}


class LeanProject:
    def __init__(self, path, lake=None, *, setup_timeout=180):
        self.path = Path(path).resolve()
        self.lake = which(lake or "lake")
        if self.lake is None:
            raise ValueError("Lake was not found. Install Lean 4.19.0 or pass --lake.")
        self.sources = {name: (self.path / name).read_bytes() for name in PINNED_SOURCES}
        for name, checksum in PINNED_SOURCES.items():
            if digest(self.sources[name]) != checksum:
                raise ValueError(f"Lean project input differs from the pinned source: {name}.")
        trusted = {name: digest(self.sources[name]) for name in
                   ("lean-toolchain", "lakefile.toml", "lake-manifest.json", "RMFlow/Expected.lean", "RMFlow/Audit.lean")}
        self.environment = {"toolchain": TOOLCHAIN, "sha256": fingerprint(trusted), "inputs": trusted}
        self.expected_source = self.sources["RMFlow/Expected.lean"]
        self.lean, self.dependencies, self.version = self._installation(setup_timeout)

    def _installation(self, timeout):
        deadline = monotonic() + timeout

        def run(command):
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise ValueError("Lean toolchain discovery exceeded its time limit.")
            return self._run(command, self.path, timeout=remaining).stdout

        script = (
            "import json, os, shutil; "
            "print(json.dumps([shutil.which('lean'), os.environ.get('LEAN_PATH', '')]))"
        )
        location, search_path = json.loads(run([self.lake, "-q", "env", sys.executable, "-c", script]))
        if not location:
            raise ValueError("Lake could not locate the pinned Lean toolchain.")
        version = run([location, "--version"]).strip()
        if "version 4.19.0," not in version or "commit 6caaee842e94," not in version:
            raise ValueError("Lean must use the pinned Lean 4.19.0 release.")
        packages = (self.path / ".lake" / "packages").resolve()
        paths = [(self.path / entry).resolve() for entry in search_path.split(os.pathsep) if entry]
        dependencies = [str(entry) for entry in paths if entry.is_relative_to(packages)]
        if not dependencies:
            raise ValueError("Compile the pinned Mathlib imports from the supplied case project.")
        prefix = Path(run([location, "--print-prefix"]).strip())
        executable = prefix / "bin" / ("lean.exe" if os.name == "nt" else "lean")
        return str(executable), dependencies, version

    @staticmethod
    def _run(command, cwd, *, env=None, allow_rejected=False, timeout=180):
        try:
            result = subprocess.run(command, cwd=cwd, env=env, text=True, encoding="utf-8", errors="replace",
                                    capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as error:
            raise ValueError("The Lean command exceeded its time limit.") from error
        if result.returncode != 0 and not (allow_rejected and result.returncode == 1):
            reason = (result.stdout + "\n" + result.stderr).strip()[-4000:]
            raise ValueError(f"Lean could not complete the check. {reason}")
        return result

    def read_report(self, candidate="describe"):
        modules, actual = ((), None) if candidate == "describe" else CANDIDATES[candidate]
        with TemporaryDirectory(prefix="rm-lean-check-") as directory:
            root = Path(directory)
            (root / "RMFlow").mkdir()
            # Recompile these exact source bytes in a fresh directory. Lake's
            # project-local build artifacts are outside the compiler search path.
            env = os.environ.copy()
            env["LEAN_PATH"] = os.pathsep.join([directory, *self.dependencies])
            env["LEAN_SYSROOT"] = str(Path(self.lean).parent.parent)
            env["PATH"] = os.pathsep.join([str(Path(self.lean).parent), os.defpath])
            for module in ("Expected", *modules):
                source = root / "RMFlow" / f"{module}.lean"
                source.write_bytes(self.sources[f"RMFlow/{module}.lean"])
                self._run([self.lean, "-o", str(source.with_suffix(".olean")), str(source)],
                          root, env=env)
            audit = root / "RMFlow" / "Audit.lean"
            audit.write_bytes(self.sources["RMFlow/Audit.lean"])
            result = self._run([self.lean, "--run", str(audit), candidate], root, env=env,
                               allow_rejected=True)
        try:
            report = json.loads(result.stdout)
        except ValueError as error:
            raise ValueError(f"Lean did not return an inspection report. {(result.stdout + result.stderr)[-2000:]}") from error
        if (not isinstance(report, dict) or report.get("expected_declaration") != EXPECTED_THEOREM
                or report.get("candidate_declaration") != actual or not isinstance(report.get("expected_type"), str)):
            raise ValueError("The Lean report does not match the requested statement and candidate.")
        if actual is not None:
            if report.get("compiled") is not True or type(report.get("scope_matches")) is not bool:
                raise ValueError("The Lean report is missing the kernel or scope check.")
            if type(report.get("accepted")) is not bool:
                raise ValueError("The Lean report is missing a verdict.")
            axioms = report.get("dependencies", {}).get("axioms")
            if not isinstance(axioms, list) or any(not isinstance(name, str) for name in axioms):
                raise ValueError("The Lean report is missing its actual axiom dependencies.")
            if report["accepted"] and (result.returncode != 0 or not report["scope_matches"]
                                       or report.get("kernel_rechecked") is not True
                                       or any(report["dependencies"].get(field) != []
                                              for field in ("unauthorized_axioms", "unapproved", "unsafe"))
                                       or set(axioms) - AXIOMS or candidate != "real"):
                raise ValueError("The Lean report's verdict contradicts the pinned proof policy.")
        return {**report, "tool_version": self.version, "checker": CHECKER}

    def candidate_bundle(self, candidate):
        modules, _ = CANDIDATES[candidate]
        return {f"RMFlow/{module}.lean": self.sources[f"RMFlow/{module}.lean"].decode("utf-8")
                for module in modules}
