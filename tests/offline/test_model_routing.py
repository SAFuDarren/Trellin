# SPDX-License-Identifier: Apache-2.0
"""The published routing command uses the exact scored configuration and tier."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline
SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "model_routing.py"
SPEC = importlib.util.spec_from_file_location("model_routing", SCRIPT)
routing = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(routing)


def test_exact_configuration_at_floor_and_pending_exclusion():
    def row(model, effort, score, cost, status="scored"):
        return {
            "model": model,
            "effort": effort,
            "score": score,
            "benchmark_usd_per_task": cost,
            "status": status,
        }

    floor = row("model-a", "medium", 45, 0.5)
    best_score = row("model-b", "high", 60, 1.5)
    registry = {
        "minimum_score": {"D2": 45},
        "configurations": [
            best_score,
            row("model-a", "low", 44, 0.1),
            row("model-c", "high", None, None, "pending"),
            floor,
        ],
    }
    assert routing.eligible_models(registry, "D2") == [floor, best_score]


@pytest.mark.parametrize("level", routing.LEVELS)
def test_normal_entry_point_reads_repository_registry(level):
    result = subprocess.run([sys.executable, str(SCRIPT), level], check=True, text=True, capture_output=True)
    assert result.stdout.startswith(f"{level}: Artificial Analysis Coding Agent Index")
    assert "/benchmark task" in result.stdout
