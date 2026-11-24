"""Enforce layering boundaries, keep docs/architecture-map.md current, and test impact radius (A1-A3, P1.7)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "architecture_map.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("architecture_map", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


arch = _load_module()


@pytest.mark.offline
def test_no_new_reverse_or_kernel_to_lean_imports():
    graph = arch.scan_package(arch.DEFAULT_PACKAGE_ROOT)
    report = arch.check_boundaries(graph)
    assert report.ok, report.format_errors()


@pytest.mark.offline
def test_architecture_map_is_up_to_date():
    graph = arch.scan_package(arch.DEFAULT_PACKAGE_ROOT)
    expected = arch.render_markdown(graph)
    actual = arch.DEFAULT_MAP_PATH.read_text(encoding="utf-8")
    assert actual == expected, (
        "docs/architecture-map.md drifted from source imports. "
        "Run `python scripts/architecture_map.py write`."
    )


def _write_pkg(root: Path, files: dict[str, str]) -> Path:
    pkg = root / "research_multiplier"
    for rel, content in files.items():
        target = pkg / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return pkg


@pytest.mark.offline
def test_prohibited_boundary_fixtures_fail_and_recorded_baseline_passes(tmp_path):
    base_files = {
        "cli/__init__.py": "",
        "cli/app.py": "from research_multiplier.workflows import core\n",
        "cli/workbench_cli.py": "from research_multiplier.workbench.server import serve\n",
        "workbench/__init__.py": "",
        "workbench/server.py": (
            "from research_multiplier.workflows import core\n"
            "from research_multiplier.cli._output import json_default\n"
            "def serve(): pass\n"
        ),
        "workflows/__init__.py": "",
        "workflows/core.py": "from research_multiplier.storage import db\n",
        "storage/__init__.py": "",
        "storage/db.py": "VALUE = 1\n",
        "proof/__init__.py": "",
        "proof/lean_check.py": "from research_multiplier.storage import db\n",
    }
    pkg = _write_pkg(tmp_path, base_files)
    baseline_reverse = frozenset({("workbench/server.py", "research_multiplier.cli._output.json_default")})

    # Recorded baseline and allowed public launcher pass cleanly.
    report = arch.check_boundaries(
        arch.scan_package(pkg),
        allowed_reverse=baseline_reverse,
        allowed_kernel_to_lean=frozenset(),
        allowed_lean_kernel_modules=frozenset(),
    )
    assert report.ok, report.format_errors()

    # A new reverse import (workflows -> cli) is caught.
    (pkg / "workflows" / "core.py").write_text(
        "from research_multiplier.cli import app\n", encoding="utf-8",
    )
    report = arch.check_boundaries(
        arch.scan_package(pkg),
        allowed_reverse=baseline_reverse,
        allowed_kernel_to_lean=frozenset(),
        allowed_lean_kernel_modules=frozenset(),
    )
    assert not report.ok
    assert report.new_reverse_imports == (("workflows/core.py", "research_multiplier.cli.app"),)

    # A new kernel-to-Lean import (storage -> proof) is caught.
    (pkg / "workflows" / "core.py").write_text("from research_multiplier.storage import db\n", encoding="utf-8")
    (pkg / "storage" / "db.py").write_text("from research_multiplier.proof import lean_check\n", encoding="utf-8")
    report = arch.check_boundaries(
        arch.scan_package(pkg),
        allowed_reverse=baseline_reverse,
        allowed_kernel_to_lean=frozenset(),
        allowed_lean_kernel_modules=frozenset(),
    )
    assert not report.ok
    assert report.new_kernel_to_lean_imports == (("storage/db.py", "research_multiplier.proof.lean_check"),)

    # A new Lean-named module inside a kernel package is caught.
    (pkg / "storage" / "db.py").write_text("VALUE = 1\n", encoding="utf-8")
    (pkg / "storage" / "lean_extra.py").write_text("EXTRA = 1\n", encoding="utf-8")
    report = arch.check_boundaries(
        arch.scan_package(pkg),
        allowed_reverse=baseline_reverse,
        allowed_kernel_to_lean=frozenset(),
        allowed_lean_kernel_modules=frozenset(),
    )
    assert not report.ok
    assert report.new_lean_named_kernel_modules == ("storage/lean_extra.py",)

    # Removing a recorded reverse import without shrinking the allowlist is flagged as stale.
    (pkg / "storage" / "lean_extra.py").unlink()
    (pkg / "workbench" / "server.py").write_text(
        "from research_multiplier.workflows import core\ndef serve(): pass\n", encoding="utf-8",
    )
    report = arch.check_boundaries(
        arch.scan_package(pkg),
        allowed_reverse=baseline_reverse,
        allowed_kernel_to_lean=frozenset(),
        allowed_lean_kernel_modules=frozenset(),
    )
    assert not report.ok
    assert report.stale_reverse_imports == (("workbench/server.py", "research_multiplier.cli._output.json_default"),)


@pytest.mark.offline
def test_impact_radius_reports_direct_and_transitive_dependents(tmp_path):
    pkg = _write_pkg(tmp_path, {
        "storage/__init__.py": "",
        "storage/db.py": "VALUE = 1\n",
        "workflows/__init__.py": "",
        "workflows/core.py": "from research_multiplier.storage import db\n",
        "workbench/__init__.py": "",
        "workbench/app.py": "from research_multiplier.workflows import core\n",
        "models/__init__.py": "",
        "models/adapter.py": "NAME = 'x'\n",
    })
    graph = arch.scan_package(pkg)
    radius = arch.impact_radius(graph, ["research_multiplier/storage/db.py"])
    assert radius["direct_packages"] == ["storage"]
    assert radius["affected_packages"] == ["workbench", "workflows"]
    assert radius["total_package_radius"] == 3
    assert radius["direct_modules"] == ["research_multiplier.storage.db"]
    assert radius["affected_modules"] == [
        "research_multiplier.workbench.app",
        "research_multiplier.workflows.core",
    ]
    assert radius["total_module_radius"] == 3
    assert radius["layers_touched"] == ["entry", "kernel", "workflows"]


@pytest.mark.offline
def test_architecture_map_cli_check_and_impact_json():
    checked = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "check"],
        capture_output=True, text=True, check=False,
    )
    assert checked.returncode == 0, checked.stderr

    queried = subprocess.check_output(
        [sys.executable, str(SCRIPT_PATH), "impact", "research_multiplier/protocol/serialization.py", "--json"],
        text=True,
    )
    payload = json.loads(queried)
    assert payload["direct_packages"] == ["protocol"]
    assert "workflows" in payload["affected_packages"]
    assert payload["total_module_radius"] >= 2
