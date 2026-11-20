"""Keep new imports pointed from entry points toward shared workflows (A1–A2)."""

import ast
from importlib.util import resolve_name
from pathlib import Path

import pytest


PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "research_multiplier"
FORBIDDEN_LAYERS = {
    "cli": ("workbench",),
    "workbench": ("cli",),
    "workflows": ("cli", "workbench"),
}
# The workbench command calls the server's public launcher.
WORKBENCH_LAUNCHER = ("cli/workbench_cli.py", "research_multiplier.workbench.server.serve")

# Existing reverse dependencies tracked by #16. Delete an entry when its import is removed.
EXISTING_REVERSE_IMPORTS = {
    ("workbench/server.py", "research_multiplier.cli._output.json_default"),
    ("workflows/setup.py", "research_multiplier.cli.lean_cli._prepare"),
}


def imported_names(path: Path, package: str):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = resolve_name("." * node.level + (node.module or ""), package)
            yield from (f"{module}.{alias.name}" for alias in node.names)


@pytest.mark.offline
def test_no_new_reverse_imports():
    violations = set()
    for layer, forbidden_layers in FORBIDDEN_LAYERS.items():
        forbidden_packages = tuple(f"research_multiplier.{name}" for name in forbidden_layers)
        for path in (PACKAGE_ROOT / layer).rglob("*.py"):
            source = path.relative_to(PACKAGE_ROOT)
            package = ".".join(("research_multiplier", *source.parent.parts))
            for name in imported_names(path, package):
                if any(name == forbidden or name.startswith(forbidden + ".") for forbidden in forbidden_packages):
                    violations.add((source.as_posix(), name))

    new_violations = sorted(violations - EXISTING_REVERSE_IMPORTS - {WORKBENCH_LAUNCHER})
    assert not new_violations, "New reverse imports (A1–A2):\n" + "\n".join(
        f"  {source} -> {name}" for source, name in new_violations
    )
