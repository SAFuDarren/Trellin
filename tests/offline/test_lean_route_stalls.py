# SPDX-License-Identifier: Apache-2.0

import pytest

from research_multiplier.proof import lean_search_session


pytestmark = pytest.mark.offline


def test_replay_does_not_carry_forward_stalls_from_unusable_proposals():
    route = {
        "implication_id": "reduction",
        "status": "awaiting_obligations",
        "obligations": [{"proposition": "A", "lemma_id": None}, {"proposition": "B", "lemma_id": None}],
    }
    unavailable = {
        "status": "proposal_failed",
        "focus": {"method": "key_lemma", "implication_id": "reduction", "proposition": "A"},
        "route_checkpoint": {"verified": 0, "pending": 2, "stalled": 1},
    }
    blocked = {
        "status": "blocked",
        "focus": {"method": "key_lemma", "implication_id": "reduction", "proposition": "B"},
        "route_checkpoint": {"verified": 0, "pending": 2, "stalled": 2},
    }

    prior_stall = {**blocked, "route_checkpoint": {"verified": 0, "pending": 2, "stalled": 1}}
    old_tail_failure = {**unavailable, "route_checkpoint": {"verified": 0, "pending": 2, "stalled": 2}}
    new_failure = {"status": "proposal_failed", "focus": unavailable["focus"], "failure_code": "timeout"}
    for history in ([unavailable, blocked], [prior_stall, old_tail_failure], [prior_stall, new_failure]):
        comparison, = lean_search_session.lane_states(history, [route], 2)
        assert comparison["stalled_checkpoints"] == 1
        assert comparison["stalled_attempts"] == 1
        next_focus, = lean_search_session.parallel_foci(history, [route], 1, 2)
        assert next_focus["proposition"] == "A"

    route["obligations"][0]["lemma_id"] = "checked"
    comparison, = lean_search_session.lane_states([unavailable, blocked], [route], 1)
    assert comparison["stalled_checkpoints"] == 0
    assert not comparison["paused"]
