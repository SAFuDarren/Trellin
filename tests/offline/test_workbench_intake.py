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
