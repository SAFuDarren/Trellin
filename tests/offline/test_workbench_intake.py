from contextlib import nullcontext
import json
from types import SimpleNamespace
from uuid import UUID

import pytest

from research_multiplier.model_adapters import ModelConfig
from research_multiplier.models.model_errors import ModelFailure, ModelOutputError
from research_multiplier.storage.postgres import DatabaseOperationError
from research_multiplier.workflows import intake, workbench_intake


pytestmark = pytest.mark.offline

_ID = "01234567-89ab-cdef-0123-456789abcdef"
_GOAL_ID = "01234567-89ab-cdef-0123-456789abcdee"
_DIGEST = "a" * 64
_NOTES = "  Every natural number n satisfies n + 0 = n.\n"
_DRAFT = {"title": "Arithmetic notes", "domain": "lean-core", "objective": "Organize the zero law",
          "notes": _NOTES, "model": "a-local-model:latest"}
_REVIEW = {"id": _ID, "sha256": _DIGEST, "actor": "Researcher", "reason": "Read the exact notes",
           "decisions": {"i1": {"action": "omit", "reason": "Need its formal statement"}}}


def _saved_draft(*, target=None, sensitivity="restricted"):
    body = {"title": _DRAFT["title"], "domain": _DRAFT["domain"], "objective": _DRAFT["objective"],
            "sensitivity": sensitivity,
            "model": {"call": {"parameters": {"private_model": {"transport": "ollama-loopback"}}}},
            "items": [{"id": "i1", "kind": "theorem", "natural": _NOTES.strip(), "formal": None}]}
    if target is not None:
        body["target"] = target
    return {"id": UUID(_ID), "intake_sha256": _DIGEST, "goal_id": None, "body": body,
            "comparison": [{"id": "i1", "status": "confirm_scope"}]}


def test_catalog_filters_cloud_names_aliases_and_embedding_models(monkeypatch):
    rows = [
        {"name": _DRAFT["model"], "size": 1},
        {"name": "embedding:latest", "size": 1},
        {"name": "cloud:latest", "size": 1},
        {"name": "remote-alias:latest", "size": 1, "remote_host": "https://example.invalid"},
        {"name": "not-on-disk:latest", "size": 0},
        {"name": "invalid # model", "size": 2},
    ]
    inspected = []
    monkeypatch.setattr(workbench_intake, "_catalog", lambda: rows)

    def show(url, document, headers, timeout, *, use_proxy):
        assert url == "http://127.0.0.1:11434/api/show" and not use_proxy
        selector = document["model"]
        inspected.append(selector)
        return {"capabilities": ["embedding" if selector.startswith("embedding") else "completion"],
                "details": {"family": "local-family"}}

    monkeypatch.setattr(workbench_intake, "post_json", show)
    assert workbench_intake.models() == {"models": [_DRAFT["model"]]}
    assert inspected == ["a-local-model:latest:local", "embedding:latest:local"]


def test_draft_reuses_private_anchored_intake_and_keeps_original_notes(monkeypatch):
    monkeypatch.setattr(workbench_intake, "_catalog", lambda: [{"name": _DRAFT["model"], "size": 1}])
    monkeypatch.setattr(workbench_intake, "post_json", lambda *args, **kwargs: {
        "capabilities": ["completion"], "details": {"family": "family"}})
    store = object()

    def draft(passed_store, raw, *, confirm_local, model_config):
        assert passed_store is store and confirm_local and model_config.private
        assert model_config.model == "a-local-model:latest:local"
        assert json.loads(raw) == {key: _DRAFT[key] for key in ("title", "domain", "objective", "notes")} | {
            "sensitivity": "restricted"}
        return _saved_draft()

    monkeypatch.setattr(intake, "draft", draft)
    actual = workbench_intake.draft(store, _DRAFT)
    assert actual["id"] == _ID and actual["sha256"] == _DIGEST and actual["confirmed"] is False
    assert actual["items"][0]["status"] == "confirm_scope"
    assert "model" not in actual


@pytest.mark.parametrize(("override", "code"), [
    ({"base_url": "https://external.invalid"}, "intake.invalid_input"),
    ({"notes": "字" * 6001}, "intake.invalid_input"),
    ({"model": "unknown:latest"}, "intake.model_unavailable"),
])
def test_invalid_input_never_produces_a_model_call(monkeypatch, override, code):
    monkeypatch.setattr(workbench_intake, "_catalog", lambda: [])
    monkeypatch.setattr(intake, "draft", lambda *args, **kwargs: pytest.fail("unexpected model request"))
    with pytest.raises(workbench_intake.IntakeError, match=code):
        workbench_intake.draft(object(), _DRAFT | override)


@pytest.mark.parametrize(("failure", "code"), [
    (ModelFailure("timeout", "sensitive provider error"), "intake.model_failed"),
    (ModelOutputError("sensitive output", SimpleNamespace(metadata={}, transcript={})), "intake.model_output"),
])
def test_model_failures_are_sanitized(monkeypatch, failure, code):
    monkeypatch.setattr(workbench_intake, "_catalog", lambda: [{"name": _DRAFT["model"], "size": 1}])
    monkeypatch.setattr(workbench_intake, "_model_config", lambda name: object())

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(intake, "draft", fail)
    with pytest.raises(workbench_intake.IntakeError) as rejected:
        workbench_intake.draft(object(), _DRAFT)
    assert str(rejected.value) == code


@pytest.mark.parametrize(("sensitivity", "confirm_local"), [("public", False), ("restricted", True)])
def test_environment_adapter_uses_the_existing_intake_scope(monkeypatch, sensitivity, confirm_local):
    configured = object()
    candidate = {"kind": "theorem", "natural": _NOTES.strip(), "formal": None}
    reply = SimpleNamespace(output={"items": [candidate]}, metadata={}, transcript={})

    def from_env(role, *, private):
        assert (role, private) == ("EXPLORE", confirm_local)
        return configured

    def complete(config, *args):
        assert config is configured
        return reply

    monkeypatch.setattr(intake.ModelConfig, "from_env", from_env)
    monkeypatch.setattr(intake, "complete_structured", complete)
    _, items = intake._extract({**_DRAFT, "sensitivity": sensitivity}, confirm_local=confirm_local)
    assert items == [{**candidate, "origin": "model"}]


def test_injected_adapter_must_match_intake_scope(monkeypatch):
    monkeypatch.setattr(intake, "complete_structured", lambda *args, **kwargs: pytest.fail("public model in private intake"))
    remote_route = ModelConfig("ollama-native", "test:latest", "family", "http://127.0.0.1:11434")
    with pytest.raises(ValueError, match="match the selected intake scope"):
        intake._extract({**_DRAFT, "sensitivity": "restricted"}, confirm_local=True, model_config=remote_route)


def test_project_model_null_question_means_omitted_without_relaxing_source_guard(monkeypatch):
    config = ModelConfig("ollama-native", "test:latest:local", "family", "http://127.0.0.1:11434", private=True)
    exact = _NOTES.strip()
    candidate = {"kind": "theorem", "natural": exact, "formal": None, "question": None}
    result = SimpleNamespace(output={"items": [candidate]}, metadata={}, transcript={})
    monkeypatch.setattr(intake, "complete_structured", lambda *args, **kwargs: result)
    notes = {**_DRAFT, "sensitivity": "restricted"}

    _, items = intake._extract(notes, confirm_local=True, model_config=config)
    assert items == [{"kind": "theorem", "natural": exact, "formal": None, "origin": "model"}]
    assert candidate["question"] is None

    candidate["natural"] = "Paraphrasing is still rejected."
    with pytest.raises(ModelOutputError, match="verbatim"):
        intake._extract(notes, confirm_local=True, model_config=config)


def test_review_uses_exact_stored_draft_and_human_decisions(monkeypatch):
    store = object()
    monkeypatch.setattr(intake, "show", lambda *_: _saved_draft())

    def approve(passed_store, identifier, checksum, decisions, actor, reason):
        assert passed_store is store and identifier == UUID(_ID) and checksum == _DIGEST
        assert {"decisions": decisions, "actor": actor, "reason": reason} == {
            key: _REVIEW[key] for key in ("decisions", "actor", "reason")}
        return {"id": UUID(_GOAL_ID)}

    monkeypatch.setattr(intake, "confirm", approve)
    assert workbench_intake.confirm(store, _REVIEW) == {"id": _ID, "goal_id": _GOAL_ID, "confirmed": True}


@pytest.mark.parametrize("saved", [_saved_draft(target="other scope"), _saved_draft(sensitivity="public")])
def test_project_review_cannot_confirm_a_different_intake_route(monkeypatch, saved):
    monkeypatch.setattr(intake, "show", lambda *_: saved)
    monkeypatch.setattr(intake, "confirm", lambda *_: pytest.fail("outside project-level local intake"))
    with pytest.raises(workbench_intake.IntakeError, match="intake.review_conflict"):
        workbench_intake.confirm(object(), _REVIEW)


def test_expected_database_rejection_is_fixed(monkeypatch):
    monkeypatch.setattr(intake, "show", lambda *_: _saved_draft())

    def reject(*args):
        raise DatabaseOperationError("human", "23514", "the user's exact theorem")

    monkeypatch.setattr(intake, "confirm", reject)
    with pytest.raises(workbench_intake.IntakeError) as rejection:
        workbench_intake.confirm(object(), _REVIEW)
    assert str(rejection.value) == "intake.review_conflict"


_CONNECTION_ID = "e17ae088-acb0-42aa-aa9f-3282b91cc58f"
_PUBLIC_DRAFT = {key: value for key, value in _DRAFT.items() if key != "model"} | {"connection_id": _CONNECTION_ID}


@pytest.fixture
def public_intake(monkeypatch, tmp_path):
    families = {"codex-cli": "openai", "claude-code": "claude"}
    settings = {"provider": "codex-cli", "model": "test-model", "fingerprint": "route revision one"}

    class Registry:
        def resolve(self, identifier):
            if identifier != _CONNECTION_ID:
                raise workbench_intake.ConnectionError("missing")
            provider, model = settings["provider"], settings["model"]
            config = ModelConfig(provider, model, families[provider], "", binary="echo", timeout_seconds=60)
            return config, {"id": identifier, **settings}

        def use(self, identifier):
            return nullcontext(self.resolve(identifier))

    researcher = UUID(_GOAL_ID)
    store = SimpleNamespace(workspace_id=UUID(_ID), local_user=lambda: {"id": researcher})
    monkeypatch.setattr(workbench_intake.paths, "object_store", lambda: tmp_path)
    return workbench_intake.PublicIntake(Registry()), settings, store


@pytest.mark.parametrize("provider", ["codex-cli", "claude-code"])
def test_public_intake_authorizes_one_exact_excerpt_and_keeps_existing_review(monkeypatch, tmp_path, public_intake, provider):
    service, settings, store = public_intake
    settings["provider"] = provider
    calls = []
    proposition = "  ∀ n : Nat, n + 0 = n\n"
    candidate = {"kind": "theorem", "natural": _NOTES.strip(), "formal": {"lean": proposition}}
    normalized = {**candidate, "formal": {"lean": {"proposition": proposition}}}
    artifacts = workbench_intake.Artifacts(tmp_path, workspace=False)
    store.json = lambda value: value

    def draft_query(role, query, body):
        assert role == "human" and "rm.draft_research_intake" in query
        assert list(body["items"]) == [{**normalized, "id": "i1", "origin": "model"}]
        assert body["sensitivity"] == "public"
        assert json.loads(artifacts.read(body["notes"]))["notes"] == _NOTES
        ref = body["model"]["call"]["parameters"]["workbench_public_intake"]
        assert artifacts.read_json(ref)["researcher_id"] == _GOAL_ID
        assert artifacts.read_json(ref)["token"] == preview["token"]
        assert artifacts.read_json(body["model"]["transcript"]) == {"items": [candidate]}
        return [{**_saved_draft(), "body": body}]

    def complete(config, role, system, outgoing, output_limit, deadline):
        calls.append(outgoing)
        assert role == "intake-notes" and config.provider == provider
        assert system == intake.EXTRACT_PROMPT
        assert 'Use "items" as the only top-level key' in system
        assert outgoing == preview["outgoing"]
        assert config.timeout_seconds == 60 and output_limit == intake.MODEL_OUTPUT_TOKENS
        audit_file, = tmp_path.iterdir()
        assert json.loads(audit_file.read_text())["visibility"] == "public"
        result = {"items": [candidate]}
        return SimpleNamespace(output=result, metadata={"parameters": {}}, transcript=result)

    store.query = draft_query
    monkeypatch.setattr(intake, "complete_structured", complete)
    preview = service.preview(store, _PUBLIC_DRAFT)
    assert preview["provider"] == provider and preview["model"] == "test-model"
    assert preview["outgoing"] == {"domain": _DRAFT["domain"], "objective": _DRAFT["objective"], "notes": _NOTES}
    assert preview["limits"]["timeout_seconds"] == 60
    assert preview["limits"]["output_token_limit_enforced"] is False
    assert not calls and not list(tmp_path.iterdir())

    authorized = {**_PUBLIC_DRAFT, "token": preview["token"]}
    saved = service.draft(store, authorized)
    assert saved["id"] == _ID and saved["items"][0]["status"] == "confirm_scope"
    assert saved["items"][0]["formal"] == normalized["formal"]
    with pytest.raises(workbench_intake.IntakeError, match="intake.release_stale"):
        service.draft(store, authorized)
    assert calls == [preview["outgoing"]]


def test_legacy_lean_extraction_keeps_model_form_until_explicit_review(monkeypatch):
    candidate = {"kind": "theorem", "natural": _NOTES.strip(), "formal": {"lean": "True"}}
    reply = SimpleNamespace(output={"items": [candidate]})
    monkeypatch.setattr(intake.ModelConfig, "from_env", lambda *args, **kwargs: object())
    monkeypatch.setattr(intake, "complete_structured", lambda *args: reply)
    _, items = intake._extract({**_DRAFT, "sensitivity": "public"})
    assert items == [{**candidate, "origin": "model"}]


@pytest.mark.parametrize("change", ["notes", "objective", "title", "workspace", "researcher", "model"])
def test_changed_public_intake_requires_a_new_preview(monkeypatch, tmp_path, public_intake, change):
    service, settings, store = public_intake
    preview = service.preview(store, _PUBLIC_DRAFT)
    request = {**_PUBLIC_DRAFT, "token": preview["token"]}
    if change == "workspace":
        store.workspace_id = UUID(_GOAL_ID)
    elif change == "researcher":
        store.local_user = lambda: {"id": UUID(_ID)}
    elif change == "model":
        settings.update(provider="claude-code", fingerprint="route revision two")
    else:
        request[change] += " changed"
    monkeypatch.setattr(intake, "draft", lambda *args, **kwargs: pytest.fail("unexpected public call"))
    with pytest.raises(workbench_intake.IntakeError, match="intake.release_stale"):
        service.draft(store, request)
    assert not list(tmp_path.iterdir())


def test_public_intake_rejects_restricted_input_or_unbound_identity(monkeypatch, public_intake):
    service, _, store = public_intake
    monkeypatch.setattr(intake, "draft", lambda *args, **kwargs: pytest.fail("unexpected public call"))
    with pytest.raises(workbench_intake.IntakeError, match="intake.invalid_input"):
        service.preview(store, {**_PUBLIC_DRAFT, "sensitivity": "restricted"})
    store.local_user = lambda: None
    with pytest.raises(workbench_intake.IntakeError, match="intake.identity_required"):
        service.preview(store, _PUBLIC_DRAFT)


def test_public_intake_reports_a_removed_connection_without_calling_the_model(monkeypatch, public_intake):
    service, _, store = public_intake
    monkeypatch.setattr(intake, "draft", lambda *args, **kwargs: pytest.fail("unexpected public call"))
    with pytest.raises(workbench_intake.IntakeError, match="connection.missing"):
        service.preview(store, {**_PUBLIC_DRAFT, "connection_id": _GOAL_ID})


def test_public_intake_fails_without_retrying_or_releasing_the_same_preview(monkeypatch, tmp_path, public_intake):
    service, _, store = public_intake
    preview = service.preview(store, _PUBLIC_DRAFT)
    attempts = []

    def timeout(*args, **kwargs):
        attempts.append(1)
        raise ModelFailure("timeout", "Detailed error stays out of the workbench")

    monkeypatch.setattr(intake, "draft", timeout)
    authorized = {**_PUBLIC_DRAFT, "token": preview["token"]}
    with pytest.raises(workbench_intake.IntakeError, match="connection.timeout"):
        service.draft(store, authorized)
    with pytest.raises(workbench_intake.IntakeError, match="intake.release_stale"):
        service.draft(store, authorized)
    audit_file, = tmp_path.iterdir()
    assert json.loads(audit_file.read_text())["token"] == preview["token"]
    assert attempts == [1]


@pytest.mark.parametrize(("code", "expected"), [
    ("model_unavailable", "connection.model_unavailable"),
    ("project_budget_exhausted", "intake.project_budget_exhausted"),
    ("adapter", "intake.public_failed"),
])
def test_public_intake_sanitizes_provider_failure(monkeypatch, public_intake, code, expected):
    service, _, store = public_intake
    preview = service.preview(store, _PUBLIC_DRAFT)

    def reject(*args, **kwargs):
        raise ModelFailure(code, "Detailed response stays out of the workbench")

    monkeypatch.setattr(intake, "draft", reject)
    with pytest.raises(workbench_intake.IntakeError) as failure:
        service.draft(store, {**_PUBLIC_DRAFT, "token": preview["token"]})
    assert str(failure.value) == expected


def test_public_draft_review_requires_the_saved_authorization(monkeypatch, tmp_path):
    saved = _saved_draft(sensitivity="public")
    workspace_objects = workbench_intake.Artifacts(tmp_path, workspace=False)
    ref = workspace_objects.put({"kind": "workbench_public_intake"})
    saved["body"]["model"]["call"]["parameters"] = {"workbench_public_intake": ref}
    monkeypatch.setattr(workbench_intake.paths, "object_store", lambda: tmp_path)
    monkeypatch.setattr(intake, "show", lambda *_: saved)
    monkeypatch.setattr(intake, "confirm", lambda *_: {"id": UUID(_GOAL_ID)})
    assert workbench_intake.confirm(object(), _REVIEW)["confirmed"]
    (tmp_path / ref["sha256"]).unlink()
    with pytest.raises(workbench_intake.IntakeError, match="intake.review_conflict"):
        workbench_intake.confirm(object(), _REVIEW)


_MATCH_ID = "01234567-89ab-cdef-0123-456789abcd01"
_TARGET_ID = "01234567-89ab-cdef-0123-456789abcd02"
_CLAIM_ID = "01234567-89ab-cdef-0123-456789abcd03"


def _review_store(*, identity_installed=True, local_user=None, matched_rows=()):
    if local_user is None and identity_installed:
        local_user = {"id": UUID(_GOAL_ID), "display_name": "Bound Researcher"}

    def query(role, sql, *params):
        assert role == "human"
        if "rm.formal_item_status" in sql:
            return list(matched_rows)
        raise AssertionError(f"Unexpected query: {sql}")

    return SimpleNamespace(
        local_identity_installed=lambda: identity_installed,
        local_user=lambda: local_user,
        query=query,
    )


def _notes_draft(*, with_reuse=True, target=None, goal_id=None):
    items = [
        {"id": "i1", "kind": "theorem", "natural": _NOTES.strip(),
         "formal": {"lean": {"proposition": "∀ n : Nat, n + 0 = n"}}, "origin": "notes"},
    ]
    comparison = [{"id": "i1", "status": "confirm_meaning"}]
    if with_reuse:
        items.append({
            "id": "i2", "kind": "definition", "natural": "Zero is a natural number.",
            "formal": {"lean": {"proposition": "(0 : Nat) = 0"}}, "origin": "notes",
        })
        comparison.append({"id": "i2", "status": "reuse", "match_version_id": _MATCH_ID})
    body = {
        "format_version": 1, "title": _DRAFT["title"], "domain": _DRAFT["domain"],
        "objective": _DRAFT["objective"], "sensitivity": "restricted",
        "notes": {"sha256": _DIGEST, "bytes": 42}, "items": items,
    }
    if target is not None:
        body["target"] = target
    row = {
        "id": UUID(_ID), "intake_sha256": _DIGEST, "goal_id": UUID(goal_id) if goal_id else None,
        "body": body, "comparison": comparison, "recorded_at": "2026-11-25T09:00:00+00:00",
    }
    if goal_id:
        row.update(actor_ref="Bound Researcher", reason="Confirmed scope",
                   decisions={"i1": {"action": "accept", "reason": "Matches notes"}},
                   confirmed_at="2026-11-25T09:05:00+00:00")
    return row


def _matched_row(*, admitted=True, needs_recheck=False, natural="Zero is a natural number."):
    return {
        "id": UUID(_MATCH_ID), "logical_key": "fixture:zero", "version": 2,
        "version_sha256": "b" * 64, "admitted": admitted, "needs_recheck": needs_recheck,
        "body": {"kind": "definition", "natural": natural, "formal": {"lean": {"proposition": "(0 : Nat) = 0"}}},
    }


def test_review_detail_enriches_reused_versions_and_omits_raw_notes(monkeypatch):
    store = _review_store(matched_rows=[_matched_row()])
    monkeypatch.setattr(intake, "show", lambda *_: _notes_draft())

    detail = workbench_intake.review_detail(store, UUID(_ID))
    assert detail["id"] == _ID and detail["sha256"] == _DIGEST
    assert detail["status"] == "pending" and detail["confirmed"] is False
    assert detail["outstanding_count"] == 1 and detail["reused_count"] == 1
    assert detail["target"] is None and detail["confirmation"] is None
    assert "notes" not in detail
    assert detail["items"][0]["requires_decision"] is True
    assert detail["items"][1]["requires_decision"] is False
    assert detail["items"][1]["matched_item"]["logical_key"] == "fixture:zero"
    assert detail["items"][1]["matched_item"]["version"] == 2
    assert detail["items"][1]["matched_item"]["stale"] is False


def test_review_detail_flags_stale_reused_version(monkeypatch):
    store = _review_store(matched_rows=[_matched_row(needs_recheck=True)])
    monkeypatch.setattr(intake, "show", lambda *_: _notes_draft())

    detail = workbench_intake.review_detail(store, UUID(_ID))
    assert detail["status"] == "stale"
    assert detail["items"][1]["matched_item"]["stale"] is True


def test_review_detail_includes_target_and_detects_superseded_and_confirmed(monkeypatch):
    store = _review_store(matched_rows=[])
    target_ref = {"id": _TARGET_ID, "sha256": "c" * 64}
    target_row = {
        "id": UUID(_TARGET_ID), "target_sha256": "c" * 64,
        "intake_id": None, "claim_id": None,
        "body": {"statement": "n + 0 = n", "formal": {"lean": {"proposition": "∀ n : Nat, n + 0 = n"}},
                 "quantifiers": [], "parameters": []},
    }
    monkeypatch.setattr(intake, "show", lambda *_: _notes_draft(with_reuse=False, target=target_ref))
    monkeypatch.setattr(intake, "target", lambda *_: target_row)

    pending = workbench_intake.review_detail(store, UUID(_ID))
    assert pending["status"] == "pending"
    assert pending["target"]["statement"] == "n + 0 = n"
    assert pending["claim_id"] is None

    target_row["intake_id"] = UUID(_GOAL_ID)
    target_row["claim_id"] = UUID(_CLAIM_ID)
    superseded = workbench_intake.review_detail(store, UUID(_ID))
    assert superseded["status"] == "superseded"
    assert superseded["claim_id"] == _CLAIM_ID

    target_row["intake_id"] = UUID(_ID)
    monkeypatch.setattr(intake, "show", lambda *_: _notes_draft(with_reuse=False, target=target_ref, goal_id=_GOAL_ID))
    confirmed = workbench_intake.review_detail(store, UUID(_ID))
    assert confirmed["status"] == "confirmed"
    assert confirmed["confirmed"] is True and confirmed["claim_id"] == _CLAIM_ID
    assert confirmed["confirmation"]["actor"] == "Bound Researcher"


def test_review_detail_reports_missing_intake(monkeypatch):
    monkeypatch.setattr(intake, "show", lambda *_: (_ for _ in ()).throw(ValueError("missing")))
    with pytest.raises(workbench_intake.IntakeReviewError, match="intakeReview.missing"):
        workbench_intake.review_detail(_review_store(), UUID(_ID))


def test_review_confirm_accepts_notes_draft_and_uses_bound_researcher(monkeypatch):
    store = _review_store(identity_installed=True, local_user={"id": UUID(_GOAL_ID), "display_name": "Bound Researcher"})
    monkeypatch.setattr(intake, "show", lambda *_: _notes_draft(with_reuse=False))
    recorded = {}

    def approve(passed_store, identifier, checksum, decisions, actor, reason):
        recorded.update(store=passed_store, id=identifier, sha256=checksum,
                        decisions=decisions, actor=actor, reason=reason)
        return {"id": UUID(_GOAL_ID)}

    monkeypatch.setattr(intake, "confirm", approve)
    payload = {
        "id": _ID, "sha256": _DIGEST, "actor": "Forged Name", "reason": "Verified against notes",
        "decisions": {"i1": {"action": "accept", "reason": "Exact quotation"}},
    }
    result = workbench_intake.review_confirm(store, payload)
    assert result == {"id": _ID, "goal_id": _GOAL_ID, "claim_id": None, "confirmed": True}
    assert recorded["actor"] == "Bound Researcher"
    assert recorded["reason"] == "Verified against notes"


def test_review_confirm_handles_target_draft_and_returns_claim_id(monkeypatch):
    store = _review_store()
    target_ref = {"id": _TARGET_ID, "sha256": "c" * 64}
    monkeypatch.setattr(intake, "show", lambda *_: _notes_draft(with_reuse=False, target=target_ref))
    monkeypatch.setattr(intake, "confirm", lambda *_: {"id": UUID(_GOAL_ID)})
    monkeypatch.setattr(intake, "target", lambda *_: {"id": UUID(_TARGET_ID), "claim_id": UUID(_CLAIM_ID)})

    result = workbench_intake.review_confirm(store, {
        "id": _ID, "sha256": _DIGEST, "reason": "Starting scope checked",
        "decisions": {"i1": {"action": "omit", "reason": "Out of scope"}},
    })
    assert result == {"id": _ID, "goal_id": _GOAL_ID, "claim_id": _CLAIM_ID, "confirmed": True}


def test_review_confirm_enforces_identity_and_legacy_actor(monkeypatch):
    monkeypatch.setattr(intake, "show", lambda *_: _notes_draft(with_reuse=False))
    monkeypatch.setattr(intake, "confirm", lambda *_: {"id": UUID(_GOAL_ID)})
    body = {"id": _ID, "sha256": _DIGEST, "reason": "Checked", "decisions": {}}

    unbound = SimpleNamespace(local_identity_installed=lambda: True, local_user=lambda: None)
    with pytest.raises(workbench_intake.IntakeReviewError, match="intakeReview.identity"):
        workbench_intake.review_confirm(unbound, body)

    legacy = _review_store(identity_installed=False)
    with pytest.raises(workbench_intake.IntakeReviewError, match="intakeReview.invalid"):
        workbench_intake.review_confirm(legacy, body)

    assert workbench_intake.review_confirm(legacy, {**body, "actor": "Legacy Researcher"})["confirmed"] is True


def test_review_confirm_validates_decisions_lean_syntax_and_sanitizes_conflicts(monkeypatch):
    store = _review_store()
    monkeypatch.setattr(intake, "show", lambda *_: _notes_draft(with_reuse=False))
    monkeypatch.setattr(intake, "confirm", lambda *_: pytest.fail("must not reach confirm"))

    with pytest.raises(workbench_intake.IntakeReviewError, match="intakeReview.invalid"):
        workbench_intake.review_confirm(store, {
            "id": _ID, "sha256": _DIGEST, "reason": "Checked",
            "decisions": {"i1": {"action": "omit", "reason": "Skip", "formal": {"lean": {"proposition": "True"}}}},
        })

    with pytest.raises(workbench_intake.IntakeReviewError, match="intakeReview.invalid"):
        workbench_intake.review_confirm(store, {
            "id": _ID, "sha256": _DIGEST, "reason": "Checked",
            "decisions": {"i1": {"action": "accept", "reason": "Bad Lean",
                                 "formal": {"lean": {"proposition": "∀ n : ℕ, n = n"}}}},
        })

    with pytest.raises(workbench_intake.IntakeReviewError, match="intakeReview.stale"):
        workbench_intake.review_confirm(store, {
            "id": _ID, "sha256": "b" * 64, "reason": "Checked", "decisions": {},
        })

    def reject(*_):
        raise DatabaseOperationError("human", "23514", "sensitive theorem wording")

    monkeypatch.setattr(intake, "confirm", reject)
    with pytest.raises(workbench_intake.IntakeReviewError) as failure:
        workbench_intake.review_confirm(store, {"id": _ID, "sha256": _DIGEST, "reason": "Checked", "decisions": {}})
    assert str(failure.value) == "intakeReview.stale"


def test_review_confirm_preserves_public_intake_authorization_check(monkeypatch, tmp_path):
    saved = _saved_draft(sensitivity="public")
    workspace_objects = workbench_intake.Artifacts(tmp_path, workspace=False)
    ref = workspace_objects.put({"kind": "workbench_public_intake"})
    saved["body"]["model"]["call"]["parameters"] = {"workbench_public_intake": ref}
    monkeypatch.setattr(workbench_intake.paths, "object_store", lambda: tmp_path)
    monkeypatch.setattr(intake, "show", lambda *_: saved)
    monkeypatch.setattr(intake, "confirm", lambda *_: {"id": UUID(_GOAL_ID)})
    store = _review_store()
    payload = {
        "id": _ID, "sha256": _DIGEST, "reason": "Checked",
        "decisions": {"i1": {"action": "omit", "reason": "Skip"}},
    }
    assert workbench_intake.review_confirm(store, payload)["confirmed"] is True
    (tmp_path / ref["sha256"]).unlink()
    with pytest.raises(workbench_intake.IntakeReviewError, match="intakeReview.stale"):
        workbench_intake.review_confirm(store, payload)
