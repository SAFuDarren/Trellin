"""Project-level intake over the existing human-reviewed notes workflow."""

from http.client import HTTPConnection, HTTPException
import json
import re
from uuid import UUID

from research_multiplier.model_adapters import ModelConfig
from research_multiplier.models.model_errors import ModelFailure, ModelOutputError
from research_multiplier.models.model_http import post_json
from research_multiplier.storage.postgres import DatabaseOperationError

from . import intake


_OLLAMA_HOST = "127.0.0.1"
_OLLAMA_PORT = 11434
_OLLAMA_URL = f"http://{_OLLAMA_HOST}:{_OLLAMA_PORT}"
_CATALOG_TIMEOUT = 2
_CATALOG_BYTES = 262_144
_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,248}")
_CLOUD_NAME = re.compile(r"(^|[-:/])cloud($|[-:/])", re.IGNORECASE)
_SHA256 = re.compile(r"[0-9a-f]{64}")


class IntakeError(ValueError):
    """A fixed workbench translation key, never a model or driver message."""


def _catalog():
    # The fixed numeric loopback connection avoids proxies and redirects;
    # this endpoint sees model inventory metadata only.
    connection = HTTPConnection(_OLLAMA_HOST, _OLLAMA_PORT, timeout=_CATALOG_TIMEOUT)
    try:
        connection.request("GET", "/api/tags", headers={"Accept": "application/json"})
        with connection.getresponse() as reply:
            if reply.status != 200:
                return []
            raw = reply.read(_CATALOG_BYTES + 1)
        data = json.loads(raw) if len(raw) <= _CATALOG_BYTES else None
    except (OSError, HTTPException, ValueError, RecursionError):
        return []
    finally:
        connection.close()
    entries = data.get("models") if isinstance(data, dict) else None
    return entries if isinstance(entries, list) else []


def _candidate(row):
    if not isinstance(row, dict):
        return None
    name, size = row.get("name"), row.get("size")
    if (not isinstance(name, str) or not _MODEL_NAME.fullmatch(name) or name.endswith(":local")
            or _CLOUD_NAME.search(name) or type(size) is not int or size <= 0
            or row.get("remote_host") or row.get("remote_model")):
        return None
    return name


def _model_config(name):
    # Ollama's explicit :local source refuses remote manifests even if the
    # daemon offers cloud models. Probe the exact selector before sending notes.
    selector = f"{name}:local"
    try:
        details = post_json(f"{_OLLAMA_URL}/api/show", {"model": selector}, {},
                            _CATALOG_TIMEOUT, use_proxy=False)
        capabilities = details.get("capabilities")
        model_details = details.get("details")
        family = model_details.get("family") if isinstance(model_details, dict) else None
        if (not isinstance(capabilities, list) or "completion" not in capabilities
                or details.get("remote_host") or details.get("remote_model")
                or not isinstance(family, str) or not family.strip()):
            return None
        return ModelConfig("ollama-native", selector, family, _OLLAMA_URL, private=True)
    except ModelFailure:
        return None


def models():
    candidates = sorted({name for row in _catalog() if (name := _candidate(row)) is not None}, key=str.casefold)
    return {"models": [name for name in candidates if _model_config(name) is not None]}


def _shape(document, fields):
    if not isinstance(document, dict) or document.keys() != fields:
        raise IntakeError("intake.invalid_input")


def _text(value, limit):
    if not isinstance(value, str) or not value.strip() or "\x00" in value or len(value) > limit:
        raise IntakeError("intake.invalid_input")
    return value.strip()


def _record(row):
    body = row["body"]
    reviewed = {item["id"]: item for item in row["comparison"]}
    return {"id": str(row["id"]), "sha256": row["intake_sha256"],
            **{key: body[key] for key in ("title", "domain", "objective")},
            "items": [{**item, **reviewed[item["id"]]} for item in body["items"]],
            "confirmed": row.get("goal_id") is not None}


def recent(store):
    rows = store.query("human", """
        SELECT id, body, comparison, intake_sha256, goal_id
        FROM rm.research_intake_status
        WHERE body ->> 'sensitivity' = 'restricted' AND NOT (body ? 'target')
          AND body #> '{model,call,parameters,private_model}' IS NOT NULL
        ORDER BY (goal_id IS NOT NULL), recorded_at DESC, id
        LIMIT 12
    """)
    return {"drafts": [_record(row) for row in rows]}


def draft(store, document):
    _shape(document, {"title", "domain", "objective", "notes", "model"})
    title = _text(document["title"], 300)
    domain = _text(document["domain"], 100)
    objective = _text(document["objective"], 4000)
    notes = document["notes"]
    _text(notes, intake.MAX_MODEL_NOTES_BYTES)
    name = _text(document["model"], 249)
    try:
        if len(notes.encode("utf-8")) > intake.MAX_MODEL_NOTES_BYTES:
            raise IntakeError("intake.invalid_input")
        data = json.dumps({"title": title, "domain": domain, "objective": objective, "notes": notes,
                           "sensitivity": "restricted"}, ensure_ascii=False).encode("utf-8")
    except UnicodeError:
        raise IntakeError("intake.invalid_input") from None
    if not any(_candidate(row) == name for row in _catalog()):
        raise IntakeError("intake.model_unavailable")
    config = _model_config(name)
    if config is None:
        raise IntakeError("intake.model_unavailable")
    try:
        return _record(intake.draft(store, data, confirm_local=True, model_config=config))
    except ModelOutputError:
        raise IntakeError("intake.model_output") from None
    except ModelFailure:
        raise IntakeError("intake.model_failed") from None
    except DatabaseOperationError:
        raise
    except ValueError:
        raise IntakeError("intake.invalid_input") from None


def confirm(store, document):
    _shape(document, {"id", "sha256", "decisions", "actor", "reason"})
    id_text = _text(document["id"], 36)
    try:
        intake_id = UUID(id_text)
    except ValueError:
        raise IntakeError("intake.invalid_input") from None
    checksum = _text(document["sha256"], 64)
    decisions = document["decisions"]
    actor = _text(document["actor"], 200)
    reason = _text(document["reason"], 2000)
    if not _SHA256.fullmatch(checksum) or not isinstance(decisions, dict) or len(decisions) > intake.MAX_ITEMS:
        raise IntakeError("intake.invalid_input")
    try:
        row = intake.show(store, intake_id)
        body = row["body"]
        model = body.get("model", {}).get("call", {}).get("parameters", {}).get("private_model")
        if body["sensitivity"] != "restricted" or "target" in body or model is None:
            raise IntakeError("intake.review_conflict")
        result = intake.confirm(store, intake_id, checksum, decisions, actor, reason)
    except DatabaseOperationError as error:
        if error.state not in {"22023", "23514", "P0002"}:
            raise
        raise IntakeError("intake.review_conflict") from None
    except ValueError:
        raise IntakeError("intake.review_conflict") from None
    return {"id": str(intake_id), "goal_id": str(result["id"]), "confirmed": True}
