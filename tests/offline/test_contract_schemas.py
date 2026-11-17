"""The JSON Schemas behind docs/interfaces.md are valid and accept or reject representative documents."""

import json
from importlib.resources import files

import pytest

jsonschema = pytest.importorskip("jsonschema")

pytestmark = pytest.mark.offline

SCHEMAS = files("research_multiplier").joinpath("schemas")
ID = "0b6b2f6e-8d1a-4c2b-9f57-3a1c2d4e5f60"
DIGEST = "a" * 64


def load(name):
    return json.loads(SCHEMAS.joinpath(name).read_text(encoding="utf-8"))


def validator(name):
    return jsonschema.Draft202012Validator(load(name))


@pytest.mark.parametrize("name", sorted(entry.name for entry in SCHEMAS.iterdir() if entry.name.endswith(".schema.json")))
def test_every_schema_is_valid_draft_2020_12(name):
    jsonschema.Draft202012Validator.check_schema(load(name))


MANIFEST = {
    "protocol_version": 1, "name": "org.example.r-verifier", "version": "0.1.0", "kind": "verifier",
    "description": "Rerun an R analysis.", "license": "Apache-2.0",
    "entry": {"command": ["Rscript", "verify.R"]},
    "environment": {"type": "system", "executables": ["Rscript"]},
    "capabilities": {"operations": ["verify"], "evidence_kinds": ["reproduced_run"]},
    "permissions": {"network": False, "external_models": False, "filesystem": "workdir"},
    "limits": {"timeout_ms": 60000, "memory_mb": 1024},
}
REQUEST = {
    "protocol_version": 1, "request_id": ID, "operation": "verify", "subject": {"claim_id": ID},
    "inputs": {"artifacts": [{"path": "proof/Main.lean", "sha256": DIGEST}],
               "basis": [{"id": ID, "version": 1, "sha256": DIGEST, "body": {}}]},
}
RESPONSE = {
    "protocol_version": 1, "request_id": ID, "status": "completed", "verdict": "verified",
    "evidence_kind": "lean", "used_basis": [ID], "artifacts": [],
    "messages": [{"level": "info", "code": "lean.checked"}],
    "plugin": {"name": "org.research-multiplier.lean", "version": "1.0.0", "source_sha256": DIGEST},
}
EVENT = {
    "id": "1024", "type": "evidence.reviewed", "schema_version": 1, "workspace_id": ID,
    "occurred_at": "2026-11-18T08:00:00Z", "actor": {"kind": "user", "id": ID},
    "subject": {"kind": "evidence", "id": ID}, "data": {},
}


@pytest.mark.parametrize("name, document", [
    ("error.schema.json", {"error": {"code": "review.not_pending", "message": "Changed.", "request_id": "r1"}}),
    ("event.schema.json", EVENT),
    ("plugin-manifest.schema.json", MANIFEST),
    ("plugin-request.schema.json", REQUEST),
    ("plugin-response.schema.json", RESPONSE),
])
def test_representative_documents_are_accepted(name, document):
    assert not list(validator(name).iter_errors(document))


@pytest.mark.parametrize("name, document", [
    ("error.schema.json", {"error": {"code": "NotDotted", "message": "x"}}),
    ("event.schema.json", {**EVENT, "occurred_at": "2026-11-18T08:00:00+08:00"}),
    ("event.schema.json", {**EVENT, "actor": {"kind": "robot"}}),
    ("plugin-manifest.schema.json", {**MANIFEST, "environment": {"type": "container"}}),
    ("plugin-manifest.schema.json", {**MANIFEST, "version": "1"}),
    ("plugin-request.schema.json", {**REQUEST, "inputs": {"artifacts": [{"path": "../outside", "sha256": DIGEST}],
                                                          "basis": []}}),
    ("plugin-response.schema.json", {key: value for key, value in RESPONSE.items()
                                     if key not in {"evidence_kind", "used_basis"}}),
])
def test_contract_violations_are_rejected(name, document):
    assert list(validator(name).iter_errors(document))
