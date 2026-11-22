# SPDX-License-Identifier: Apache-2.0
"""List dated model configurations that clear a contribution tier's score floor."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REGISTRY = Path(__file__).resolve().parents[1] / ".github" / "agent-models.json"
LEVELS = ("D1", "D2", "D3", "D4")
FIT_START = "<!-- trellin-agent-fit:start -->"
FIT_END = "<!-- trellin-agent-fit:end -->"
FIT_POINTS = re.compile(r"\*\*(D[1-4]) · (\d+)/15[^*]*\*\*.*?"
                        r"[Ss]cope ([0-3]), uncertainty ([0-3]), impact ([0-3]), "
                        r"verification ([0-3]), dependencies ([0-3])\b")
FIT_SKILLS = re.compile(r"^- \*\*Required skills(?: and (?:environment|verification))?[.:]\*\*\s+\S", re.M)


def eligible_models(registry: dict, level: str) -> list[dict]:
    minimum = registry["minimum_score"][level]
    return sorted(
        (
            config
            for config in registry["configurations"]
            if config["status"] == "scored" and config["score"] >= minimum
        ),
        key=lambda config: (config["benchmark_usd_per_task"], -config["score"], config["model"]),
    )


def load_registry() -> dict:
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def canonical(name: str) -> str:
    return re.sub(r"[\s-]+", "", name.casefold())


def model_configuration(level: str, supplied: tuple[str, str, str], fallback: str = "",
                        *, registry: dict | None = None) -> tuple[dict, dict | None]:
    registry = load_registry() if registry is None else registry
    key = tuple(canonical(part) for part in supplied)
    model = next((
        model for model in eligible_models(registry, level)
        if tuple(canonical(model[field]) for field in ("runner", "model", "effort")) == key
        and canonical(model.get("fallback", "")) == canonical(fallback)
    ), None)
    return registry, model


def validate_issue_fit(level: str, body: str) -> tuple[str, bool]:
    """Check the published issue rubric, required skills and recommendation."""
    if level not in LEVELS:
        raise ValueError("add exactly one difficulty:d1–d4 label")
    if body.count(FIT_START) != 1 or body.count(FIT_END) != 1:
        raise ValueError("add one issue-visible Task difficulty and Agent fit section")
    _, _, rest = body.partition(FIT_START)
    block, _, _ = rest.partition(FIT_END)
    points = FIT_POINTS.search(block)
    if not points or points[1] != level or int(points[2]) != sum(map(int, points.groups()[2:])):
        raise ValueError("match the difficulty label to the five scored dimensions and their total")
    minimum_level = sum(int(points[2]) >= value for value in (0, 4, 8, 12))
    if int(level[1]) < minimum_level:
        raise ValueError("difficulty is below the rubric tier for the given total")
    if not FIT_SKILLS.search(block):
        raise ValueError("add the required skills and verification environment")
    if re.search(r"^- \*\*Human (?:native )?acceptance[.:]\*\*\s+\S", block, re.M):
        return level, True
    registry = load_registry()
    benchmark = re.escape(registry["benchmark"])
    recommendation = re.search(
        r"^- \*\*Recommended configuration(?: for implementation or review)?[.:]\*\*\s+"
        r"([^/\n]+) / ([^/\n]+) / ([^,\s]+)(?: \(([^)\n]+)\))?,\s+\*\*(\d+)\*\*.*"
        rf"\[{benchmark}\]\(https://[^\n]+checked \d{{4}}-\d{{2}}-\d{{2}}", block, re.M,
    )
    if not recommendation:
        raise ValueError("add a dated runner, model, effort and benchmark score, or a human acceptance path")
    _, profile = model_configuration(level, (recommendation[1], recommendation[2], recommendation[3]),
                                     recommendation[4] or "", registry=registry)
    if not profile or profile["score"] != int(recommendation[5]):
        raise ValueError("refresh the recommendation from the eligible scored model registry")
    return level, False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("level", choices=LEVELS)
    args = parser.parse_args()
    registry = load_registry()
    print(
        f"{args.level}: {registry['benchmark']} >= {registry['minimum_score'][args.level]} "
        f"(checked {registry['checked_on']})"
    )
    for config in eligible_models(registry, args.level):
        fallback = f" ({config['fallback']})" if config.get("fallback") else ""
        print(
            f"  {config['runner']} / {config['model']} / {config['effort']}{fallback}  "
            f"{config['score']}  ${config['benchmark_usd_per_task']:.2f}/benchmark task"
        )
    print("Confirm required tools and skills on the issue. D3/D4 require maintainer confirmation.")


if __name__ == "__main__":
    main()
