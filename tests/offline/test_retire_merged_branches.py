# SPDX-License-Identifier: Apache-2.0

import sys
from pathlib import Path

import pytest

scripts = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(scripts))
from retire_merged_branches import eligible_branch  # noqa: E402

pytestmark = pytest.mark.offline


@pytest.fixture
def row():
    return {
        "branch": "fix/8",
        "head": "tip",
        "state": "ready_to_retire",
        "claim": None,
        "dependent_open_prs": [],
        "pull_request": {"number": 8, "state": "merged", "final_head": "tip", "base": "main", "merge_commit": "merge"},
    }


def test_exact_merged_branch_with_live_recovery_ref_is_eligible(row):
    assert eligible_branch(row, "main", "now", set(), {8: "tip"}, lambda merge, head: (merge, head) == ("merge", "now"))


@pytest.mark.parametrize("risk", ["claim", "dependent", "occupied", "tip", "closed", "no_recovery", "no_ancestry"])
def test_retirement_holds_uncertain_branches(row, risk):
    occupied = {"fix/8"} if risk == "occupied" else set()
    pull_heads = {} if risk == "no_recovery" else {8: "tip"}
    if risk == "claim":
        row["claim"] = {"agent": "worker"}
    if risk == "dependent":
        row["dependent_open_prs"] = [9]
    if risk == "tip":
        row["head"] = "new"
    if risk == "closed":
        row["pull_request"]["state"] = "closed"
    assert not eligible_branch(row, "main", "now", occupied, pull_heads, lambda *_: risk != "no_ancestry")
