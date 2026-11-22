# SPDX-License-Identifier: Apache-2.0
"""List dated model configurations that clear a contribution tier's score floor."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REGISTRY = Path(__file__).resolve().parents[1] / ".github" / "agent-models.json"
LEVELS = ("D1", "D2", "D3", "D4")


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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("level", choices=LEVELS)
    args = parser.parse_args()
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
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
