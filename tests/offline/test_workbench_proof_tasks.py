"""Exercise the workbench's signed proof policy boundary without a model."""

from uuid import UUID

import pytest

from research_multiplier.workflows import workbench_proof_tasks as tasks


pytestmark = pytest.mark.offline
CLAIM, PACKET, STATEMENT, ITEM, POLICY = (UUID(int=value) for value in range(1, 6))
DIGEST = "a" * 64


class ProofStore:
    def __init__(self):
        self.basis_digest = DIGEST
        self.policy = None
        self.receipt = None
        self.roles = None

    def query(self, role, sql, *values):
        assert role == "human"
        if "pg_advisory_xact_lock" in sql:
            assert values == (f"workbench-proof:{CLAIM}",)
            return []
        if "get_packet_basis" in sql:
            return [{"item_version_id": ITEM, "version_sha256": self.basis_digest, "packet_label": "H1",
                     "logical_key": "starting-basis", "statement": "Given True.", "formal": {"lean": "True"}}]
        if "research_progress_status" in sql:
            assert values == (CLAIM, str(PACKET), str(STATEMENT), DIGEST)
            return [self.policy] if self.policy else []
        if "evidence_foundation_status" in sql:
            return [self.receipt] if self.receipt else []
        raise AssertionError(sql)

    def verify_runtime_roles(self, *, roles):
        self.roles = roles


@pytest.fixture
def proof_context(monkeypatch):
    store = ProofStore()
    record = {
        "title": "An exact local proof", "status": "open", "scope": {"proof_ready": True, "lean_proposition": "True"},
        "details": {"domain": "lean-core", "sensitivity": "public", "contract_sha256": DIGEST,
                    "lean": {"id": str(STATEMENT), "check_ready": True, "statement_sha256": DIGEST},
                    "packet": {"id": str(PACKET), "packet_sha256": DIGEST}, "progress_policy_id": None},
        "next": {"action": "continue_proof_work"}, "conclusion": None,
        "decisions": {"items": []}, "reductions": {"items": []},
    }
    monkeypatch.setattr(tasks.research_resume, "snapshot", lambda *_args, **_options: record)
    document = {"claim_id": str(CLAIM), "token": tasks.snapshot(store, CLAIM)["scope"]["token"],
                "model_rounds": 0, "reason": "Use the confirmed basis."}
    return store, record, document


def _policy(rounds=0):
    return {"id": POLICY, "policy_sha256": DIGEST, "state": "awaiting_evidence",
            "stopped_at": None, "model_rounds": rounds, "private_model": None, "phase": None}


def test_start_signs_only_the_shown_scope_and_reuses_the_recorded_approval(monkeypatch, proof_context):
    store, _, document = proof_context
    changes = []

    def propose(specification, *, store):
        changes.append(("proposal", specification, store))
        return _policy()

    def approve(identifier, signature, actor, reason, *, store):
        changes.append(("approval", identifier, signature, actor, reason, store))
        store.policy = _policy()

    monkeypatch.setattr(tasks.research_progress, "propose", propose)
    monkeypatch.setattr(tasks.research_progress, "approve", approve)
    assert tasks.start(store, document) == {"policy_id": str(POLICY)}
    assert changes == [
        ("proposal", {"packet_id": str(PACKET), "lean_statement_id": str(STATEMENT), "metric": "exact-closure",
                      "actions": ["lean"], "lean_model_rounds": 0}, store),
        ("approval", POLICY, DIGEST, "Trellin local researcher", document["reason"], store),
    ]
    assert tasks.start(store, document) == {"policy_id": str(POLICY)}
    assert [item[0] for item in changes] == ["proposal", "approval", "proposal"]
    assert store.roles == ("scheduler", "verifier")


def test_invalid_or_changed_scope_and_public_model_are_rejected(proof_context):
    store, record, document = proof_context
    with pytest.raises(tasks.ProofTaskError, match="proof_task.invalid"):
        tasks.start(store, {**document, "model_rounds": True})
    with pytest.raises(tasks.ProofTaskError, match="proof_task.invalid"):
        tasks.start(store, {**document, "actor": "Caller-supplied name"})
    with pytest.raises(tasks.ProofTaskError, match="proof_task.local_unavailable"):
        tasks.start(store, {**document, "model_rounds": 1})
    store.policy = _policy(rounds=1)
    with pytest.raises(tasks.ProofTaskError, match="proof_task.policy_conflict"):
        tasks.start(store, document)
    store.basis_digest = "b" * 64
    with pytest.raises(tasks.ProofTaskError, match="proof_task.stale"):
        tasks.start(store, document)
    record["details"]["lean"]["check_ready"] = False
    assert tasks.snapshot(store, CLAIM)["blocker"] == "proof_task.lean_unconfirmed"
    assert store.roles is None


def test_local_model_rounds_require_and_retain_the_exact_profile(monkeypatch, proof_context):
    store, record, document = proof_context
    private = {"config_sha256": "c" * 64, "model": "checked-local-profile"}
    record["details"]["sensitivity"] = "restricted"
    monkeypatch.setattr(tasks, "_local_model", lambda sensitivity: private if sensitivity == "restricted" else None)
    proposed = []

    def propose(specification, *, store):
        assert specification["lean_model_scope"] == "local" and specification["lean_model_rounds"] == 2
        proposed.append(specification)
        return _policy(rounds=2)

    monkeypatch.setattr(tasks.research_progress, "propose", propose)
    monkeypatch.setattr(tasks.research_progress, "approve", lambda *_args, **_kwargs: None)
    assert tasks.start(store, {**document, "model_rounds": 2}) == {"policy_id": str(POLICY)}
    assert store.roles == ("scheduler", "verifier", "agent")

    store.policy = {**_policy(rounds=2), "private_model": {"config_sha256": "d" * 64, "model": "old-local-profile"}}
    with pytest.raises(tasks.ProofTaskError, match="proof_task.local_unavailable"):
        tasks.start(store, {**document, "model_rounds": 2})
    assert len(proposed) == 1


def test_stop_checks_claim_policy_and_never_confuses_verified_with_adopted(monkeypatch, proof_context):
    store, record, _ = proof_context
    store.policy = _policy()
    command = {"claim_id": str(CLAIM), "policy_id": str(POLICY), "reason": "Pause this proof."}
    stops = []
    monkeypatch.setattr(tasks.research_progress, "stop", lambda *args, **kwargs: stops.append((args, kwargs)))
    with pytest.raises(tasks.ProofTaskError, match="proof_task.stale"):
        tasks.stop(store, {**command, "policy_id": str(PACKET)})
    assert tasks.stop(store, command) == {"policy_id": str(POLICY)}
    assert stops == [((POLICY, command["reason"]), {"store": store})]

    store.receipt = {"id": ITEM, "review_status": "pending", "kind": "lean"}
    assert tasks.snapshot(store, CLAIM)["state"] == "verified"
    assert tasks.snapshot(store, CLAIM)["adopted"] is False
    with pytest.raises(tasks.ProofTaskError, match="proof_task.stale"):
        tasks.stop(store, command)
    record["conclusion"] = {"reference": {"evidence_id": str(ITEM)}}
    store.receipt["review_status"] = "approved"
    assert tasks.snapshot(store, CLAIM)["adopted"] is True
