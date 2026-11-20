"""Exercise standalone runtime pinning with the native verifier."""

import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest

from research_multiplier import general_lean, lean_mathlib, lean_native_runtime


class NativeLeanRuntimeTests(unittest.TestCase):
    def test_new_bytes_cannot_reuse_a_frozen_runtime_identity(self):
        installed = lean_native_runtime.NativeLean(general_lean._executable(None))
        with TemporaryDirectory(prefix="rm-lean-runtime-regression-") as directory:
            root = Path(directory)
            runtime_files = [Path("bin/lean")]
            runtime_files.extend(Path("lib/lean") / path for path in installed.modules)
            binaries = [Path(key.removeprefix("toolchain:")) for key in installed.native["file_images"]
                        if key.startswith("toolchain:")]
            self.assertTrue(binaries)
            for relative in [*runtime_files, *binaries]:
                origin = (installed.sysroot / relative).resolve(strict=True)
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.link(origin, destination)
                except OSError:
                    shutil.copy2(origin, destination)

            lean = root / "bin" / "lean"
            pin = lean_native_runtime.NativeLean(lean)
            self.assertEqual(pin.digest, installed.digest)
            audit, environment = general_lean._environment(lean, native=pin)
            report, _ = general_lean._check(lean, audit, "True", (), general_lean._type_source("True", ()),
                                            "True.intro", {}, native=pin)
            self.assertTrue(report["kernel_rechecked"])
            self.assertEqual(environment["inputs"]["native_runtime_sha256"], pin.digest)

            module = root / "lib" / "lean" / "Lean.olean"
            module_original = module.read_bytes()
            source = root / "Lean.lean"
            original_source = (installed.sysroot / "src" / "lean" / "Lean.lean").read_text(encoding="utf-8")
            source.write_text(original_source + "\ndef rmNativeRuntimeRegressionMarker : Nat := 0\n", encoding="utf-8")
            compiled = root / "Lean.olean"
            result = subprocess.run(
                [str(lean), "-o", str(compiled), source.name], cwd=root,
                env=lean_mathlib._environment(lean, root, root),
                capture_output=True, text=True, timeout=30, check=False)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            compiled.replace(module)
            with self.assertRaisesRegex(ValueError, "pinned Lean toolchain module changed"):
                pin.run({}, [["--version"]])
            changed = lean_native_runtime.NativeLean(lean)
            self.assertEqual(changed.native["compiler"], pin.native["compiler"])
            self.assertNotEqual(changed.digest, pin.digest)
            self._replace(module, module_original)

            binary = root / binaries[0]
            self._replace(binary, b"unusable shared library")
            with self.assertRaisesRegex(ValueError, "native runtime libraries"):
                pin.run({}, [["--version"]])

    @staticmethod
    def _replace(path, content):
        # A staging hard link can share the original installation's inode.
        replacement = path.with_suffix(path.suffix + ".replacement")
        replacement.write_bytes(content)
        replacement.replace(path)
