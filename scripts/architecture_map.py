# SPDX-License-Identifier: Apache-2.0
"""Generate the package dependency map, enforce layering rules, and report change impact radius (P1.7)."""

from __future__ import annotations

import argparse
import ast
from collections import defaultdict, deque
from dataclasses import dataclass
from importlib.util import resolve_name
import json
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACKAGE_ROOT = REPO_ROOT / "research_multiplier"
DEFAULT_MAP_PATH = REPO_ROOT / "docs" / "architecture-map.md"

# A1 layer assignment for active subpackages under research_multiplier/.
PACKAGE_LAYERS: dict[str, str] = {
    "cli": "entry",
    "workbench": "entry",
    "workflows": "workflows",
    "storage": "kernel",
    "policy": "kernel",
    "protocol": "kernel",
    "models": "kernel",
    "sources": "kernel",
    "proof": "plugin",
    "lean_support": "plugin",
    "domain_packages": "plugin",
    "evaluations": "plugin",
}

LAYER_TITLES: dict[str, str] = {
    "entry": "Entry points (A1)",
    "workflows": "Workflows (A2)",
    "kernel": "Kernel foundation (A1, A3)",
    "plugin": "Plugins and evaluations (A1, A3, C1)",
}

PINNED_SUBDIRS: dict[str, tuple[str, ...]] = {
    "proof": ("proof", "_pinned"),
    "domain_packages": ("domain_packages", "_pinned"),
    "models": ("models", "_pinned"),
    "workflows": ("workflows", "_pinned"),
    "evaluations": ("evaluations", "_pinned"),
}

# Entry-to-workflow direction rules (A1-A2).
FORBIDDEN_LAYERS: dict[str, tuple[str, ...]] = {
    "cli": ("workbench",),
    "workbench": ("cli",),
    "workflows": ("cli", "workbench"),
}

# The workbench CLI command invokes the server's public launcher.
WORKBENCH_LAUNCHER = ("cli/workbench_cli.py", "research_multiplier.workbench.server.serve")

# Existing reverse dependencies tracked by #16. This allowlist may only shrink.
EXISTING_REVERSE_IMPORTS: frozenset[tuple[str, str]] = frozenset({
    ("workbench/server.py", "research_multiplier.cli._output.json_default"),
    ("workflows/setup.py", "research_multiplier.cli.lean_cli._prepare"),
})

# Kernel packages and non-Lean workflow modules must not add new Lean imports (A3).
KERNEL_GUARDED_PACKAGES: frozenset[str] = frozenset({
    "storage",
    "policy",
    "protocol",
    "models",
    "sources",
    "domain_packages",
    "workflows",
})

STRICT_KERNEL_PACKAGES: frozenset[str] = frozenset({
    "storage",
    "policy",
    "protocol",
    "models",
    "sources",
    "domain_packages",
})

# Existing Lean-named modules inside kernel packages (A3). This allowlist may only shrink.
EXISTING_LEAN_NAMED_KERNEL_MODULES: frozenset[str] = frozenset({
    "policy/lean_budget.py",
    "policy/lean_model_context.py",
    "policy/lean_search_scope.py",
    "sources/formalpedia_lean.py",
    "storage/lean_progress.py",
})

# Existing imports from kernel / non-Lean workflow modules into Lean-specific modules (A3).
# Delete entries as modules migrate to the plugin boundary; this allowlist may only shrink.
EXISTING_KERNEL_TO_LEAN_IMPORTS: frozenset[tuple[str, str]] = frozenset({
    ("policy/scope_review.py", "research_multiplier.workflows.lean_workflow"),
    ("workflows/doctor.py", "research_multiplier.proof.lean_runner.digest"),
    ("workflows/doctor.py", "research_multiplier.proof.packaged_lean.SETUP_SOURCES"),
    ("workflows/intake.py", "research_multiplier.proof.lean_statement_syntax.require_supported"),
    ("workflows/research_followup.py", "research_multiplier.general_lean"),
    ("workflows/research_followup.py", "research_multiplier.proof.lean_proof_search"),
    ("workflows/research_progress.py", "research_multiplier.evaluations.lean_evolution"),
    ("workflows/research_progress.py", "research_multiplier.storage.lean_progress.PendingLeanPass"),
    ("workflows/research_progress.py", "research_multiplier.storage.lean_progress.hold_lean_pass"),
    ("workflows/research_progress.py", "research_multiplier.storage.lean_progress.pending_lean_passes"),
    ("workflows/research_progress.py", "research_multiplier.workflows.lean_search.search"),
    ("workflows/runtime.py", "research_multiplier.policy.lean_budget.claim"),
    ("workflows/setup.py", "research_multiplier.cli.lean_cli._prepare"),
    ("workflows/target_draft.py", "research_multiplier.lean_parameters.parameters"),
    ("workflows/workbench_activity.py", "research_multiplier.storage.lean_progress.ACTIVE_LEAN_POLICY_SQL"),
    ("workflows/workbench_lemma_plans.py", "research_multiplier.general_lean"),
    ("workflows/workbench_lemma_plans.py", "research_multiplier.workflows.lean_verification"),
})


@dataclass(frozen=True)
class ArchitectureGraph:
    packages: dict[str, tuple[str, ...]]
    package_edges: dict[str, tuple[str, ...]]
    module_edges: dict[str, tuple[str, ...]]
    reverse_imports: frozenset[tuple[str, str]]
    kernel_to_lean_imports: frozenset[tuple[str, str]]
    lean_named_kernel_modules: frozenset[str]


@dataclass(frozen=True)
class BoundaryReport:
    new_reverse_imports: tuple[tuple[str, str], ...]
    stale_reverse_imports: tuple[tuple[str, str], ...]
    new_kernel_to_lean_imports: tuple[tuple[str, str], ...]
    stale_kernel_to_lean_imports: tuple[tuple[str, str], ...]
    new_lean_named_kernel_modules: tuple[str, ...]
    stale_lean_named_kernel_modules: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not any((
            self.new_reverse_imports,
            self.stale_reverse_imports,
            self.new_kernel_to_lean_imports,
            self.stale_kernel_to_lean_imports,
            self.new_lean_named_kernel_modules,
            self.stale_lean_named_kernel_modules,
        ))

    def format_errors(self) -> str:
        sections: list[str] = []
        if self.new_reverse_imports:
            lines = "\n".join(f"  {src} -> {tgt}" for src, tgt in self.new_reverse_imports)
            sections.append(f"New reverse imports (A1-A2):\n{lines}")
        if self.stale_reverse_imports:
            lines = "\n".join(f"  {src} -> {tgt}" for src, tgt in self.stale_reverse_imports)
            sections.append(f"Stale entries in EXISTING_REVERSE_IMPORTS (remove resolved items):\n{lines}")
        if self.new_kernel_to_lean_imports:
            lines = "\n".join(f"  {src} -> {tgt}" for src, tgt in self.new_kernel_to_lean_imports)
            sections.append(f"New kernel-to-Lean imports (A3):\n{lines}")
        if self.stale_kernel_to_lean_imports:
            lines = "\n".join(f"  {src} -> {tgt}" for src, tgt in self.stale_kernel_to_lean_imports)
            sections.append(f"Stale entries in EXISTING_KERNEL_TO_LEAN_IMPORTS (remove resolved items):\n{lines}")
        if self.new_lean_named_kernel_modules:
            lines = "\n".join(f"  {path}" for path in self.new_lean_named_kernel_modules)
            sections.append(f"New Lean-named modules in kernel packages (A3):\n{lines}")
        if self.stale_lean_named_kernel_modules:
            lines = "\n".join(f"  {path}" for path in self.stale_lean_named_kernel_modules)
            sections.append(f"Stale entries in EXISTING_LEAN_NAMED_KERNEL_MODULES (remove resolved items):\n{lines}")
        return "\n\n".join(sections)


def imported_names(path: Path, package: str):
    """Yield resolved dotted import targets from a Python source file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = resolve_name("." * node.level + (node.module or ""), package)
            yield from (f"{module}.{alias.name}" for alias in node.names)


def _pinned_owners(package_root: Path) -> dict[str, str]:
    owners: dict[str, str] = {}
    for owner, parts in PINNED_SUBDIRS.items():
        folder = package_root.joinpath(*parts)
        if folder.is_dir():
            for file in folder.glob("*.py"):
                owners[file.stem] = owner
    return owners


def _module_key(rel: Path) -> str:
    if rel.name == "__init__.py":
        return ".".join(("research_multiplier", *rel.parent.parts)) if rel.parent != Path(".") else "research_multiplier"
    return ".".join(("research_multiplier", *rel.with_suffix("").parts))


def _resolve_target_package(name: str, package_root: Path, known_packages: set[str], pinned: dict[str, str]) -> str | None:
    if not name.startswith("research_multiplier."):
        return None
    top = name.split(".", 2)[1]
    if top in known_packages:
        return top
    if top in pinned:
        return pinned[top]
    return None


def _resolve_target_module(name: str, known_modules: set[str]) -> str | None:
    if not name.startswith("research_multiplier."):
        return None
    parts = name.split(".")
    for end in range(len(parts), 1, -1):
        candidate = ".".join(parts[:end])
        if candidate in known_modules:
            return candidate
    return None


def _is_lean_target(name: str, target_package: str | None) -> bool:
    if not name.startswith("research_multiplier."):
        return False
    if target_package in {"proof", "lean_support"}:
        return True
    parts = name.split(".")[1:]
    return any("lean" in part.lower() for part in parts)


def _is_lean_source(rel: Path) -> bool:
    return any(
        token in part.lower()
        for part in rel.parts
        for token in ("lean", "proof", "formalpedia")
    )


def scan_package(package_root: Path = DEFAULT_PACKAGE_ROOT) -> ArchitectureGraph:
    """Scan Python imports under package_root and collect dependency graphs and boundary edges."""
    pinned = _pinned_owners(package_root)
    known_packages = {
        entry.name
        for entry in package_root.iterdir()
        if entry.is_dir() and not entry.name.startswith((".", "_")) and entry.name not in {"migrations", "schemas"}
    }
    packages_map: dict[str, list[str]] = {pkg: [] for pkg in sorted(known_packages)}
    files: list[tuple[Path, Path, str, str]] = []
    known_modules: set[str] = set()

    for path in sorted(package_root.rglob("*.py")):
        rel = path.relative_to(package_root)
        if any(part.startswith((".", "_")) for part in rel.parts[:-1]) or rel.parts[0] == "migrations":
            continue
        if len(rel.parts) == 1:
            continue
        src_pkg = rel.parts[0]
        if src_pkg not in known_packages:
            continue
        mod = _module_key(rel)
        packages_map[src_pkg].append(rel.as_posix())
        known_modules.add(mod)
        files.append((path, rel, src_pkg, mod))

    pkg_edges: dict[str, set[str]] = {pkg: set() for pkg in known_packages}
    mod_edges: dict[str, set[str]] = {mod: set() for mod in known_modules}
    reverse_imports: set[tuple[str, str]] = set()
    kernel_to_lean: set[tuple[str, str]] = set()
    lean_named_kernel: set[str] = set()

    for path, rel, src_pkg, src_mod in files:
        rel_posix = rel.as_posix()
        if src_pkg in STRICT_KERNEL_PACKAGES and "lean" in rel.name.lower():
            lean_named_kernel.add(rel_posix)

        package_ctx = ".".join(("research_multiplier", *rel.parent.parts))
        forbidden_targets = tuple(f"research_multiplier.{layer}" for layer in FORBIDDEN_LAYERS.get(src_pkg, ()))

        for target in imported_names(path, package_ctx):
            tgt_pkg = _resolve_target_package(target, package_root, known_packages, pinned)
            if tgt_pkg is not None and tgt_pkg != src_pkg:
                pkg_edges[src_pkg].add(tgt_pkg)

            tgt_mod = _resolve_target_module(target, known_modules)
            if tgt_mod is not None and tgt_mod != src_mod:
                mod_edges[src_mod].add(tgt_mod)

            if forbidden_targets and any(
                target == forbidden or target.startswith(forbidden + ".") for forbidden in forbidden_targets
            ):
                reverse_imports.add((rel_posix, target))

            if src_pkg in KERNEL_GUARDED_PACKAGES and not _is_lean_source(rel) and _is_lean_target(target, tgt_pkg):
                kernel_to_lean.add((rel_posix, target))

    return ArchitectureGraph(
        packages={pkg: tuple(sorted(mods)) for pkg, mods in sorted(packages_map.items())},
        package_edges={pkg: tuple(sorted(targets)) for pkg, targets in sorted(pkg_edges.items())},
        module_edges={mod: tuple(sorted(targets)) for mod, targets in sorted(mod_edges.items())},
        reverse_imports=frozenset(reverse_imports),
        kernel_to_lean_imports=frozenset(kernel_to_lean),
        lean_named_kernel_modules=frozenset(lean_named_kernel),
    )


def check_boundaries(
    graph: ArchitectureGraph,
    *,
    allowed_reverse: frozenset[tuple[str, str]] = EXISTING_REVERSE_IMPORTS,
    allowed_launcher: tuple[str, str] = WORKBENCH_LAUNCHER,
    allowed_kernel_to_lean: frozenset[tuple[str, str]] = EXISTING_KERNEL_TO_LEAN_IMPORTS,
    allowed_lean_kernel_modules: frozenset[str] = EXISTING_LEAN_NAMED_KERNEL_MODULES,
) -> BoundaryReport:
    """Compare observed layering violations against shrinking allowlists."""
    observed_reverse = graph.reverse_imports - {allowed_launcher}
    return BoundaryReport(
        new_reverse_imports=tuple(sorted(observed_reverse - allowed_reverse)),
        stale_reverse_imports=tuple(sorted(allowed_reverse - observed_reverse)),
        new_kernel_to_lean_imports=tuple(sorted(graph.kernel_to_lean_imports - allowed_kernel_to_lean)),
        stale_kernel_to_lean_imports=tuple(sorted(allowed_kernel_to_lean - graph.kernel_to_lean_imports)),
        new_lean_named_kernel_modules=tuple(sorted(graph.lean_named_kernel_modules - allowed_lean_kernel_modules)),
        stale_lean_named_kernel_modules=tuple(sorted(allowed_lean_kernel_modules - graph.lean_named_kernel_modules)),
    )


def render_markdown(graph: ArchitectureGraph) -> str:
    """Render the canonical docs/architecture-map.md document from the scanned graph."""
    incoming: dict[str, set[str]] = {pkg: set() for pkg in graph.packages}
    for src, targets in graph.package_edges.items():
        for tgt in targets:
            incoming.setdefault(tgt, set()).add(src)

    lines: list[str] = [
        "# Package Architecture Map",
        "",
        "<!-- Generated by `python scripts/architecture_map.py write`. Do not edit by hand. -->",
        "",
        "Generated from static Python imports under `research_multiplier/` (excluding `_compat/`,",
        "`migrations/`, and `_pinned/` byte-frozen sources; pinned alias imports resolve to their",
        "owning subpackage). Regenerate with `python scripts/architecture_map.py write` and verify",
        "with `python scripts/architecture_map.py check` or `python -m pytest -m offline`.",
        "",
        "Decisions: **A1** (five-layer structure), **A2** (workflow business logic), **A3** (Lean as plugin).",
        "",
        "## Package dependency graph",
        "",
        "```mermaid",
        "flowchart TD",
    ]

    by_layer: dict[str, list[str]] = defaultdict(list)
    for pkg in sorted(graph.packages):
        by_layer[PACKAGE_LAYERS.get(pkg, "other")].append(pkg)

    for layer in ("entry", "workflows", "kernel", "plugin", "other"):
        members = by_layer.get(layer)
        if not members:
            continue
        title = LAYER_TITLES.get(layer, "Other packages")
        lines.append(f'    subgraph layer_{layer}["{title}"]')
        for pkg in members:
            count = len(graph.packages[pkg])
            unit = "module" if count == 1 else "modules"
            lines.append(f'        {pkg}["{pkg} ({count} {unit})"]')
        lines.append("    end")

    for src in sorted(graph.package_edges):
        for tgt in graph.package_edges[src]:
            lines.append(f"    {src} --> {tgt}")

    lines.extend([
        "```",
        "",
        "## Package dependency table",
        "",
        "| Package | Layer | Modules | Imports | Imported by |",
        "| --- | --- | ---: | --- | --- |",
    ])
    for pkg in sorted(graph.packages):
        layer = PACKAGE_LAYERS.get(pkg, "other")
        count = len(graph.packages[pkg])
        out_list = ", ".join(f"`{t}`" for t in graph.package_edges.get(pkg, ())) or "—"
        in_list = ", ".join(f"`{s}`" for s in sorted(incoming.get(pkg, ()))) or "—"
        lines.append(f"| `{pkg}` | {layer} | {count} | {out_list} | {in_list} |")

    observed_reverse = sorted(graph.reverse_imports - {WORKBENCH_LAUNCHER})
    observed_k2l = sorted(graph.kernel_to_lean_imports)
    observed_lean_kernel = sorted(graph.lean_named_kernel_modules)

    lines.extend([
        "",
        "## Enforced layering boundaries",
        "",
        "`tests/offline/test_import_boundaries.py` enforces three rules and fails on either a new",
        "forbidden import or a stale allowlist entry (baselines may only shrink):",
        "",
        "1. `cli` and `workbench` may import `workflows`, not each other's internals (except the public",
        f"   launcher `{WORKBENCH_LAUNCHER[0]}` -> `{WORKBENCH_LAUNCHER[1]}`).",
        "2. `workflows` must not import `cli` or `workbench`.",
        "3. Kernel packages (`storage`, `policy`, `protocol`, `models`, `sources`, `domain_packages`)",
        "   and non-Lean `workflows` modules must not add new imports of Lean-specific modules (`proof`,",
        "   `lean_support`, or Lean-named modules) or new Lean-named files inside kernel packages.",
        "",
        f"### Recorded reverse-entry imports ({len(observed_reverse)})",
        "",
    ])
    if observed_reverse:
        lines.extend(["| Source | Target |", "| --- | --- |"])
        for src, tgt in observed_reverse:
            lines.append(f"| `{src}` | `{tgt}` |")
    else:
        lines.append("None.")

    lines.extend([
        "",
        f"### Recorded kernel-to-Lean imports ({len(observed_k2l)})",
        "",
    ])
    if observed_k2l:
        lines.extend(["| Source | Target |", "| --- | --- |"])
        for src, tgt in observed_k2l:
            lines.append(f"| `{src}` | `{tgt}` |")
    else:
        lines.append("None.")

    lines.extend([
        "",
        f"### Recorded Lean-named kernel modules ({len(observed_lean_kernel)})",
        "",
    ])
    if observed_lean_kernel:
        for path in observed_lean_kernel:
            lines.append(f"- `{path}`")
    else:
        lines.append("None.")

    lines.extend([
        "",
        "## Measuring PR change impact radius",
        "",
        "Engineering audits (finding `A-202611-05`) measure the change impact radius of a branch or",
        "recent commits directly from this graph:",
        "",
        "```sh",
        "python scripts/architecture_map.py impact --git-range origin/main..HEAD",
        "python scripts/architecture_map.py impact --commits 10",
        "python scripts/architecture_map.py impact research_multiplier/storage/paths.py --json",
        "```",
        "",
    ])
    return "\n".join(lines)


def _reverse_closure(edges: dict[str, tuple[str, ...]], seeds: set[str]) -> set[str]:
    reverse: dict[str, set[str]] = defaultdict(set)
    for src, targets in edges.items():
        for tgt in targets:
            reverse[tgt].add(src)
    visited: set[str] = set()
    queue = deque(seeds)
    while queue:
        current = queue.popleft()
        for dependent in reverse.get(current, ()):
            if dependent not in seeds and dependent not in visited:
                visited.add(dependent)
                queue.append(dependent)
    return visited


def impact_radius(graph: ArchitectureGraph, changed_files: list[str]) -> dict:
    """Compute direct and transitive reverse-dependency impact for repo-relative changed files."""
    normalized = sorted({path.strip().replace("\\", "/") for path in changed_files if path.strip()})
    known_modules = set(graph.module_edges)
    direct_modules: set[str] = set()
    direct_packages: set[str] = set()

    for rel_str in normalized:
        if not rel_str.startswith("research_multiplier/") or not rel_str.endswith(".py"):
            continue
        sub = Path(rel_str.removeprefix("research_multiplier/"))
        if len(sub.parts) >= 2 and sub.parts[0] in graph.packages:
            direct_packages.add(sub.parts[0])
            mod = _module_key(sub)
            if mod in known_modules:
                direct_modules.add(mod)

    affected_modules = _reverse_closure(graph.module_edges, direct_modules)
    affected_packages = _reverse_closure(graph.package_edges, direct_packages)
    all_packages = direct_packages | affected_packages
    layers_touched = sorted({PACKAGE_LAYERS.get(pkg, "other") for pkg in all_packages})

    return {
        "changed_files": normalized,
        "direct_packages": sorted(direct_packages),
        "affected_packages": sorted(affected_packages),
        "total_package_radius": len(all_packages),
        "direct_modules": sorted(direct_modules),
        "affected_modules": sorted(affected_modules),
        "total_module_radius": len(direct_modules) + len(affected_modules),
        "layers_touched": layers_touched,
    }


def _git_changed_files(repo_root: Path, git_range: str) -> list[str]:
    out = subprocess.check_output(
        ["git", "-C", str(repo_root), "diff", "--name-only", git_range],
        text=True,
    )
    return [line.strip() for line in out.splitlines() if line.strip()]


def _git_recent_commits(repo_root: Path, count: int) -> list[tuple[str, str, list[str]]]:
    log = subprocess.check_output(
        ["git", "-C", str(repo_root), "log", f"-n{count}", "--format=%H\t%s"],
        text=True,
    )
    entries = []
    for line in log.splitlines():
        if not line.strip():
            continue
        sha, subject = line.split("\t", 1)
        files_out = subprocess.check_output(
            ["git", "-C", str(repo_root), "diff-tree", "--no-commit-id", "--name-only", "-r", sha],
            text=True,
        )
        files = [f.strip() for f in files_out.splitlines() if f.strip()]
        entries.append((sha, subject, files))
    return entries


def _format_impact_text(report: dict) -> str:
    direct_pkgs = ", ".join(report["direct_packages"]) or "(none)"
    affected_pkgs = ", ".join(report["affected_packages"]) or "(none)"
    layers = ", ".join(report["layers_touched"]) or "(none)"
    return "\n".join([
        f"Changed files: {len(report['changed_files'])}",
        f"Direct packages ({len(report['direct_packages'])}): {direct_pkgs}",
        f"Transitively affected packages ({len(report['affected_packages'])}): {affected_pkgs}",
        f"Total package radius: {report['total_package_radius']}",
        f"Total module radius: {report['total_module_radius']} "
        f"(direct {len(report['direct_modules'])}, affected {len(report['affected_modules'])})",
        f"Layers touched: {layers}",
    ])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    write_cmd = sub.add_parser("write", help="Regenerate docs/architecture-map.md from source imports.")
    write_cmd.add_argument("--output", type=Path, default=DEFAULT_MAP_PATH)

    check_cmd = sub.add_parser("check", help="Verify layering boundaries and architecture map freshness.")
    check_cmd.add_argument("--map", type=Path, default=DEFAULT_MAP_PATH)

    impact_cmd = sub.add_parser("impact", help="Report change impact radius for paths, a git range, or recent commits.")
    impact_cmd.add_argument("paths", nargs="*", help="Repo-relative file paths to evaluate.")
    impact_cmd.add_argument("--git-range", help="Git diff range, for example origin/main..HEAD.")
    impact_cmd.add_argument("--commits", type=int, help="Report impact radius for each of the last N commits.")
    impact_cmd.add_argument("--json", action="store_true", help="Emit JSON output.")

    args = parser.parse_args(argv)
    graph = scan_package(DEFAULT_PACKAGE_ROOT)

    if args.command == "write":
        content = render_markdown(graph)
        args.output.write_text(content, encoding="utf-8")
        print(f"Wrote {args.output.relative_to(REPO_ROOT) if args.output.is_relative_to(REPO_ROOT) else args.output}.")
        return 0

    if args.command == "check":
        boundary = check_boundaries(graph)
        errors: list[str] = []
        if not boundary.ok:
            errors.append(boundary.format_errors())
        expected = render_markdown(graph)
        actual = args.map.read_text(encoding="utf-8") if args.map.is_file() else ""
        if actual != expected:
            errors.append(
                f"{args.map} is out of date. Run `python scripts/architecture_map.py write` and commit the result."
            )
        if errors:
            print("\n\n".join(errors), file=sys.stderr)
            return 1
        print("Architecture map and layering boundaries are up to date.")
        return 0

    if args.command == "impact":
        if args.commits is not None:
            if args.commits <= 0:
                raise SystemExit("--commits must be a positive integer.")
            rows = []
            for sha, subject, files in _git_recent_commits(REPO_ROOT, args.commits):
                radius = impact_radius(graph, files)
                rows.append({"commit": sha[:12], "subject": subject, **radius})
            if args.json:
                print(json.dumps(rows, indent=2))
            else:
                for row in rows:
                    pkgs = ", ".join(row["direct_packages"]) or "non-python"
                    print(
                        f"{row['commit']}  pkgs={row['total_package_radius']:2d} "
                        f"mods={row['total_module_radius']:3d}  direct=[{pkgs}]  {row['subject']}"
                    )
            return 0

        files = list(args.paths)
        if args.git_range:
            files.extend(_git_changed_files(REPO_ROOT, args.git_range))
        if not files:
            raise SystemExit("Pass file paths, --git-range, or --commits.")
        report = impact_radius(graph, files)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(_format_impact_text(report))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
