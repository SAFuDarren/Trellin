"""Versioned deliveries for a scoped Agent work attempt."""

from typing import TypedDict

from research_multiplier.policy.contracts import ArtifactRef
from research_multiplier.storage.artifacts import Artifacts


class WorkAttempt(TypedDict):
    workspace_id: str
    task_id: str
    generation: int
    context: ArtifactRef


def publish_delivery(artifacts: Artifacts, attempt: WorkAttempt, output: ArtifactRef) -> ArtifactRef:
    """Reference the exact inputs and output of an attempt in an immutable delivery."""
    artifacts.read(attempt["context"])
    artifacts.read(output)
    return artifacts.put({"version": 1, "attempt": attempt, "output": output})


def read_delivery(artifacts: Artifacts, receipt: ArtifactRef, attempt: WorkAttempt) -> bytes:
    """Read a delivery only for the attempt provided by the authorized work record."""
    delivery = artifacts.read_json(receipt)
    if (
        not isinstance(delivery, dict)
        or delivery.keys() != {"version", "attempt", "output"}
        or delivery["version"] != 1
        or delivery["attempt"] != attempt
    ):
        raise ValueError("Delivery does not belong to this work attempt.")
    artifacts.read(attempt["context"])
    return artifacts.read(delivery["output"])
