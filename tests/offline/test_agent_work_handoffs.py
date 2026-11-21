import pytest

from research_multiplier.storage.artifacts import Artifacts
from research_multiplier.workflows.agent_work_handoffs import publish_delivery, read_delivery


pytestmark = pytest.mark.offline


def test_delivery_reopens_with_the_exact_work_attempt_and_output(tmp_path):
    artifacts = Artifacts(tmp_path, workspace=False)
    attempt = {
        "workspace_id": "project-a",
        "task_id": "implementation",
        "generation": 1,
        "context": artifacts.put({"scope": "fixture"}),
    }
    output = artifacts.put_bytes(b"Proposal for the pinned fixture.")
    receipt = publish_delivery(artifacts, attempt, output)
    assert publish_delivery(artifacts, attempt, output) == receipt

    reopened = Artifacts(tmp_path, workspace=False)
    assert read_delivery(reopened, receipt, attempt) == b"Proposal for the pinned fixture."
    for change in (
        {"workspace_id": "project-b"},
        {"task_id": "another task using the same profile"},
        {"generation": 2},
        {"context": artifacts.put({"scope": "changed"})},
    ):
        with pytest.raises(ValueError, match="does not belong"):
            read_delivery(reopened, receipt, {**attempt, **change})

    (tmp_path / output["sha256"]).write_bytes(b"An unrecorded replacement.")
    with pytest.raises(ValueError, match="checksum failed"):
        read_delivery(reopened, receipt, attempt)


def test_delivery_requires_the_original_context_to_publish_and_reopen(tmp_path):
    artifacts = Artifacts(tmp_path, workspace=False)
    context = artifacts.put({"scope": "original"})
    attempt = {"workspace_id": "project", "task_id": "write", "generation": 1, "context": context}
    output = artifacts.put_bytes(b"result")
    receipt = publish_delivery(artifacts, attempt, output)

    (tmp_path / context["sha256"]).unlink()
    with pytest.raises(FileNotFoundError):
        publish_delivery(artifacts, attempt, output)
    with pytest.raises(FileNotFoundError):
        read_delivery(artifacts, receipt, attempt)
