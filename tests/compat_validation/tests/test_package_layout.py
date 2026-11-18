"""Keep package entry points separate from implementation files."""

import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

import research_multiplier


class PackageLayoutTests(unittest.TestCase):
    def test_only_entry_files_at_package_root(self):
        package = Path(research_multiplier.__file__).resolve().parent
        self.assertEqual({entry.name for entry in package.glob("*.py")}, {"__init__.py", "__main__.py"})

    def test_documented_imports_are_package_local(self):
        distribution = Path(research_multiplier.__file__).resolve().parents[1]
        env = {**os.environ, "PYTHONPATH": str(distribution)}
        source = """
import sys
before = tuple(sys.meta_path)
import research_multiplier
from research_multiplier.migrate import upgrade as old_upgrade
from research_multiplier.storage.migrate import upgrade
from research_multiplier.production_domains import Domain as old_domain
from research_multiplier.domain_packages.production_domains import Domain
from research_multiplier import model_adapters
assert old_upgrade is upgrade and old_domain is Domain
assert model_adapters.ModelAdapter.__module__ == 'research_multiplier.model_adapters'
assert tuple(sys.meta_path) == before
"""
        with TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, "-B", "-c", source],
                                    cwd=directory, env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
