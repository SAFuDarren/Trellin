import os
import shutil

import pytest

from research_multiplier.workflows.agent_work_runner import build_delivery


pytestmark = pytest.mark.offline
REVISION = "a" * 40


@pytest.mark.skipif(shutil.which("git") is None, reason="Git is needed to verify a patch replay.")
def test_implementation_returns_replayed_patch_for_exact_selected_files():
    odd_name = 'science notes/结果 "draft".txt' if os.name != "nt" else "science notes/结果 draft.txt"
    files = {"readme.txt": "", odd_name: "before\r\nlast line", "untouched.txt": "keep\n"}
    inputs = {"revision": REVISION, "files": files}
    changes = [{"path": odd_name, "content": "after\r\nlast line"}, {"path": "readme.txt", "content": "new\n"}]
    result = {"summary": "Proposed change", "changes": changes}

    delivery = build_delivery("implement", inputs, result)

    assert delivery == build_delivery("implement", inputs, {**result, "changes": list(reversed(changes))})
    assert delivery["kind"] == "patch"
    assert delivery["summary"] == "Proposed change"
    assert delivery["checks"] == ["patch_applies"]
    assert 'diff --git "a/readme.txt" "b/readme.txt"' in delivery["body"]
    assert '-before\r\n+after\r\n' in delivery["body"]
    assert '\\ No newline at end of file' in delivery["body"]
    assert "untouched.txt" not in delivery["body"]
    assert files[odd_name] == "before\r\nlast line"


@pytest.mark.parametrize(("updates", "message"), [
    ([{"path": "../outside.txt", "content": "edit"}], "repository-relative"),
    ([{"path": ".GiT/config", "content": "edit"}], "repository-relative"),
    ([{"path": "other.txt", "content": "edit"}], "approved pinned file"),
    ([{"path": "approved.txt", "content": "first"}, {"path": "approved.txt", "content": "second"}], "listed once"),
    ([{"path": "approved.txt", "content": "old"}], "must change"),
    ([{"path": "approved.txt", "content": "binary\0"}], "UTF-8"),
    ([{"path": "approved.txt", "content": "\ud800"}], "UTF-8"),
    ([{"path": "approved.txt", "content": "字" * 35_000}], "bounded UTF-8"),
    ([], "at least one pinned file"),
])
def test_implementation_rejects_invalid_or_unapproved_proposals(updates, message):
    inputs = {"revision": REVISION, "files": {"approved.txt": "old", "also-approved.txt": "old"}}
    with pytest.raises(ValueError, match=message):
        build_delivery("implement", inputs, {"summary": "Proposal", "changes": updates})


@pytest.mark.parametrize("mode", ["review", "draft"])
def test_text_deliveries_are_distinct_from_code_patches(mode):
    assert build_delivery(mode, None, {"summary": "Result", "body": "A report.\nSecond line.\n"}) == {
        "kind": mode,
        "summary": "Result",
        "body": "A report.\nSecond line.\n",
        "checks": [],
    }
    with pytest.raises(ValueError, match="expected fields"):
        build_delivery(mode, None, {"summary": "Result", "body": "Report", "checks": ["patch_applies"]})
    with pytest.raises(ValueError, match="bounded UTF-8"):
        build_delivery(mode, None, {"summary": "Result", "body": "字" * 6_000})
