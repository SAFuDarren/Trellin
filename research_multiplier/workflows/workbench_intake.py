"""Project-level intake over the existing human-reviewed notes workflow."""

from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from http.client import HTTPConnection, HTTPException
import json
import re
from secrets import token_hex
from threading import Lock
from uuid import UUID

from research_multiplier.model_adapters import ModelConfig, PROVIDERS
from research_multiplier.models.connections import ConnectionError, provider_error_code
from research_multiplier.models.model_errors import ModelFailure, ModelOutputError
from research_multiplier.models.model_http import post_json
from research_multiplier.protocol.serialization import fingerprint
from research_multiplier.storage import paths
from research_multiplier.storage.artifacts import Artifacts
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
_ITEM_ID = re.compile(r"i[1-9][0-9]?")
_INTAKE_FIELDS = {"title", "domain", "objective", "notes"}
_REVIEW_CONFIRM_REQUIRED = {"id", "sha256", "decisions", "reason"}
_REVIEW_CONFIRM_ALLOWED = _REVIEW_CONFIRM_REQUIRED | {"actor"}
_MAX_RELEASE_PREVIEWS = 64


class IntakeError(ValueError):
    """A fixed workbench translation key, never a model or driver message."""


class IntakeReviewError(ValueError):
    """A fixed workbench intake-review error key, never a model or driver message."""


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
        WHERE NOT (body ? 'target') AND (
            (body ->> 'sensitivity' = 'restricted' AND body #> '{model,call,parameters,private_model}' IS NOT NULL)
            OR (body ->> 'sensitivity' = 'public'
                AND body #> '{model,call,parameters,workbench_public_intake}' IS NOT NULL)
        )
        ORDER BY (goal_id IS NOT NULL), recorded_at DESC, id
        LIMIT 12
    """)
    return {"drafts": [_record(row) for row in rows]}


def _draft_data(document, sensitivity):
    title = _text(document["title"], 300)
    domain = _text(document["domain"], 100)
    objective = _text(document["objective"], 4000)
    notes = document["notes"]
    _text(notes, intake.MAX_MODEL_NOTES_BYTES)
    try:
        if len(notes.encode("utf-8")) > intake.MAX_MODEL_NOTES_BYTES:
            raise IntakeError("intake.invalid_input")
        data = {"title": title, "domain": domain, "objective": objective, "notes": notes,
                "sensitivity": sensitivity}
        return data, json.dumps(data, ensure_ascii=False).encode("utf-8")
    except UnicodeError:
        raise IntakeError("intake.invalid_input") from None


def _save_draft(store, data, **model):
    try:
        return _record(intake.draft(store, data, **model))
    except ModelOutputError:
        raise IntakeError("intake.model_output") from None
    except ModelFailure as error:
        if "public_authorization" not in model:
            raise IntakeError("intake.model_failed") from None
        if error.code == "project_budget_exhausted":
            raise IntakeError("intake.project_budget_exhausted") from None
        raise IntakeError(provider_error_code(error, fallback="intake.public_failed")) from None
    except DatabaseOperationError:
        raise
    except ValueError:
        raise IntakeError("intake.invalid_input") from None


def draft(store, document):
    _shape(document, _INTAKE_FIELDS | {"model"})
    _, data = _draft_data(document, "restricted")
    name = _text(document["model"], 249)
    if not any(_candidate(row) == name for row in _catalog()):
        raise IntakeError("intake.model_unavailable")
    config = _model_config(name)
    if config is None:
        raise IntakeError("intake.model_unavailable")
    return _save_draft(store, data, confirm_local=True, model_config=config)


class PublicIntake:
    """Preview and consume authorization for one public excerpt in this workbench session."""

    def __init__(self, registry):
        self.registry = registry
        self.previews = {}
        self.lock = Lock()

    @contextmanager
    def _request(self, store, document, *, dispatch=False):
        data, raw = _draft_data(document, "public")
        try:
            identifier = str(UUID(_text(document["connection_id"], 36)))
        except ValueError:
            raise IntakeError("intake.invalid_input") from None
        try:
            researcher = store.local_user()
        except DatabaseOperationError:
            raise
        except ValueError:
            raise IntakeError("intake.identity_required") from None
        if not researcher or store.workspace_id is None:
            raise IntakeError("intake.identity_required")
        try:
            selected = self.registry.use(identifier) if dispatch else nullcontext(self.registry.resolve(identifier))
            with selected as (config, connection):
                if (config.private or config.adapter_factory or config.provider != connection["provider"]
                        or config.model != connection["model"]):
                    raise IntakeError("connection.configuration")
                outgoing = intake.model_context(data)
                limits = {
                    "max_calls": 1,
                    "timeout_seconds": min(
                        intake.MODEL_TIMEOUT_SECONDS, config.timeout_seconds or intake.MODEL_TIMEOUT_SECONDS
                    ),
                    "max_output_tokens": min(
                        intake.MODEL_OUTPUT_TOKENS, config.max_output_tokens or intake.MODEL_OUTPUT_TOKENS
                    ),
                    "output_token_limit_enforced": PROVIDERS[config.provider][0] != "cli",
                }
                authorization = {
                    "kind": "workbench_public_intake",
                    "workspace_id": str(store.workspace_id),
                    "researcher_id": str(researcher["id"]),
                    "visibility": "public",
                    "connection": connection,
                    "intake_sha256": fingerprint(data),
                    "request_sha256": fingerprint([intake.EXTRACT_PROMPT, outgoing]),
                    "limits": limits,
                }
                yield authorization, outgoing, raw, config
        except ConnectionError as error:
            raise IntakeError(error.code) from None

    def preview(self, store, document):
        _shape(document, _INTAKE_FIELDS | {"connection_id"})
        with self._request(store, document) as (authorization, outgoing, _, config):
            model = {"provider": config.provider, "model": config.model}
        token = token_hex(32)
        with self.lock:
            if len(self.previews) >= _MAX_RELEASE_PREVIEWS:
                del self.previews[next(iter(self.previews))]
            self.previews[token] = authorization
        return {"token": token, **model, "outgoing": outgoing, "limits": authorization["limits"]}

    def draft(self, store, document):
        _shape(document, _INTAKE_FIELDS | {"connection_id", "token"})
        token = _text(document["token"], 64)
        if not _SHA256.fullmatch(token):
            raise IntakeError("intake.release_stale")
        with self._request(store, document, dispatch=True) as (authorization, _, raw, config):
            with self.lock:
                if self.previews.get(token) != authorization:
                    raise IntakeError("intake.release_stale")
                del self.previews[token]
            objects = Artifacts(paths.object_store(), workspace=False)
            ref = objects.put({**authorization, "token": token, "authorized_at": datetime.now(timezone.utc).isoformat()})
            return _save_draft(store, raw, model=True, model_config=config, public_authorization=ref)


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
        parameters = body.get("model", {}).get("call", {}).get("parameters", {})
        local = body["sensitivity"] == "restricted" and parameters.get("private_model") is not None
        public = body["sensitivity"] == "public" and parameters.get("workbench_public_intake") is not None
        if "target" in body or not (local or public):
            raise IntakeError("intake.review_conflict")
        if public:
            Artifacts(paths.object_store(), workspace=False).read(parameters["workbench_public_intake"])
        result = intake.confirm(store, intake_id, checksum, decisions, actor, reason)
    except DatabaseOperationError as error:
        if error.state not in {"22023", "23514", "P0002"}:
            raise
        raise IntakeError("intake.review_conflict") from None
    except (OSError, ValueError):
        raise IntakeError("intake.review_conflict") from None
    return {"id": str(intake_id), "goal_id": str(result["id"]), "confirmed": True}


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _review_text(value, limit):
    if not isinstance(value, str) or not value.strip() or "\x00" in value or len(value) > limit:
        raise IntakeReviewError("intakeReview.invalid")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise IntakeReviewError("intakeReview.invalid") from None
    return value.strip()


def _review_formal(value):
    if not isinstance(value, dict) or not value:
        raise IntakeReviewError("intakeReview.invalid")
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        raw = encoded.encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise IntakeReviewError("intakeReview.invalid") from None
    if "\x00" in encoded or len(raw) > 12_000:
        raise IntakeReviewError("intakeReview.invalid")
    return value


def _review_decisions(decisions):
    if not isinstance(decisions, dict) or len(decisions) > intake.MAX_ITEMS:
        raise IntakeReviewError("intakeReview.invalid")
    normalized = {}
    for item_id, choice in decisions.items():
        if (not isinstance(item_id, str) or not _ITEM_ID.fullmatch(item_id)
                or not isinstance(choice, dict)
                or not ({"action", "reason"} <= set(choice) <= {"action", "reason", "formal"})):
            raise IntakeReviewError("intakeReview.invalid")
        action = choice["action"]
        if action not in ("accept", "omit"):
            raise IntakeReviewError("intakeReview.invalid")
        entry = {"action": action, "reason": _review_text(choice["reason"], 2000)}
        if "formal" in choice:
            if action == "omit":
                raise IntakeReviewError("intakeReview.invalid")
            entry["formal"] = _review_formal(choice["formal"])
        normalized[item_id] = entry
    return normalized


def _review_actor(store, document):
    if "actor" in document and document["actor"] is not None:
        supplied = document["actor"]
        if not isinstance(supplied, str) or "\x00" in supplied or len(supplied) > 200:
            raise IntakeReviewError("intakeReview.invalid")
        try:
            supplied.encode("utf-8")
        except UnicodeError:
            raise IntakeReviewError("intakeReview.invalid") from None
    if store.local_identity_installed():
        try:
            user = store.local_user()
        except DatabaseOperationError:
            raise
        except ValueError:
            raise IntakeReviewError("intakeReview.identity") from None
        if not user or not isinstance(user.get("display_name"), str) or not user["display_name"].strip():
            raise IntakeReviewError("intakeReview.identity")
        return user["display_name"].strip()
    return _review_text(document.get("actor"), 200)


def _matched_library_items(store, comparison):
    identifiers = []
    seen = set()
    for entry in comparison:
        match_id = entry.get("match_version_id")
        if match_id is None:
            continue
        key = str(match_id)
        if key not in seen:
            seen.add(key)
            identifiers.append(UUID(key))
    if not identifiers:
        return {}
    rows = store.query("human", """
        SELECT id, logical_key, version, version_sha256, body, admitted, needs_recheck
        FROM rm.formal_item_status
        WHERE id = ANY(%s::uuid[])
    """, identifiers)
    return {str(row["id"]): row for row in rows}


def _target_summary(frozen):
    body = frozen["body"]
    return {
        "id": str(frozen["id"]),
        "sha256": frozen["target_sha256"],
        "statement": body["statement"],
        "formal": body["formal"],
        "quantifiers": body.get("quantifiers", []),
        "parameters": body.get("parameters", []),
    }


def review_detail(store, intake_id):
    """Read one intake draft with matched library versions and target scope for Review."""
    try:
        row = intake.show(store, intake_id)
    except ValueError:
        raise IntakeReviewError("intakeReview.missing") from None
    body = row["body"]
    comparison = row["comparison"]
    reviewed = {entry["id"]: entry for entry in comparison}
    matched_by_id = _matched_library_items(store, comparison)

    items = []
    reused_stale = False
    outstanding_count = 0
    reused_count = 0
    for item in body["items"]:
        compared = reviewed[item["id"]]
        is_reuse = compared["status"] == "reuse"
        if is_reuse:
            reused_count += 1
        else:
            outstanding_count += 1
        merged = {**item, **compared, "requires_decision": not is_reuse}
        match_id = compared.get("match_version_id")
        if match_id is not None:
            match_key = str(match_id)
            merged["match_version_id"] = match_key
            matched = matched_by_id.get(match_key)
            if matched is None:
                stale = True
            else:
                matched_body = matched["body"]
                stale = (
                    bool(matched["needs_recheck"])
                    or not bool(matched["admitted"])
                    or matched_body.get("kind") != item.get("kind")
                    or matched_body.get("natural") != item.get("natural")
                    or matched_body.get("formal") != item.get("formal")
                )
                merged["matched_item"] = {
                    "id": match_key,
                    "logical_key": matched["logical_key"],
                    "version": matched["version"],
                    "sha256": matched["version_sha256"],
                    "kind": matched_body["kind"],
                    "natural": matched_body["natural"],
                    "formal": matched_body["formal"],
                    "admitted": bool(matched["admitted"]),
                    "stale": stale,
                }
            if is_reuse and stale:
                reused_stale = True
        items.append(merged)

    goal_id = str(row["goal_id"]) if row.get("goal_id") is not None else None
    claim_id = None
    target_info = None
    if "target" in body:
        try:
            frozen = intake.target(store, UUID(str(body["target"]["id"])))
        except ValueError:
            raise IntakeReviewError("intakeReview.missing") from None
        target_info = _target_summary(frozen)
        if goal_id is not None:
            status = "confirmed"
            claim_id = str(frozen["claim_id"]) if frozen.get("claim_id") is not None else None
        elif frozen.get("intake_id") is not None and str(frozen["intake_id"]) != str(row["id"]):
            status = "superseded"
            claim_id = str(frozen["claim_id"]) if frozen.get("claim_id") is not None else None
        else:
            status = "stale" if reused_stale else "pending"
    elif goal_id is not None:
        status = "confirmed"
    else:
        status = "stale" if reused_stale else "pending"

    confirmation = None
    if goal_id is not None:
        confirmation = {
            "actor": row.get("actor_ref"),
            "reason": row.get("reason"),
            "decisions": row.get("decisions") or {},
            "confirmed_at": _iso(row.get("confirmed_at")),
        }

    return {
        "id": str(row["id"]),
        "sha256": row["intake_sha256"],
        "title": body["title"],
        "domain": body["domain"],
        "objective": body["objective"],
        "sensitivity": body["sensitivity"],
        "recorded_at": _iso(row.get("recorded_at")),
        "status": status,
        "confirmed": goal_id is not None,
        "goal_id": goal_id,
        "claim_id": claim_id,
        "target": target_info,
        "items": items,
        "outstanding_count": outstanding_count,
        "reused_count": reused_count,
        "confirmation": confirmation,
    }


def review_confirm(store, document):
    """Confirm any pending intake draft from Review through the existing kernel workflow."""
    if not isinstance(document, dict) or not (_REVIEW_CONFIRM_REQUIRED <= set(document) <= _REVIEW_CONFIRM_ALLOWED):
        raise IntakeReviewError("intakeReview.invalid")
    id_text = _review_text(document["id"], 36)
    try:
        intake_id = UUID(id_text)
    except ValueError:
        raise IntakeReviewError("intakeReview.invalid") from None
    checksum = _review_text(document["sha256"], 64)
    if not _SHA256.fullmatch(checksum):
        raise IntakeReviewError("intakeReview.invalid")
    reason = _review_text(document["reason"], 2000)
    decisions = _review_decisions(document["decisions"])
    actor = _review_actor(store, document)

    try:
        row = intake.show(store, intake_id)
    except ValueError:
        raise IntakeReviewError("intakeReview.missing") from None
    if row["intake_sha256"] != checksum:
        raise IntakeReviewError("intakeReview.stale")

    body = row["body"]
    supplied_formal = [{"formal": choice["formal"]} for choice in decisions.values() if "formal" in choice]
    if supplied_formal:
        try:
            intake._require_checkable({"items": supplied_formal}, body["domain"])
        except ValueError:
            raise IntakeReviewError("intakeReview.invalid") from None

    parameters = body.get("model", {}).get("call", {}).get("parameters", {})
    try:
        if body.get("sensitivity") == "public" and parameters.get("workbench_public_intake") is not None:
            Artifacts(paths.object_store(), workspace=False).read(parameters["workbench_public_intake"])
        result = intake.confirm(store, intake_id, checksum, decisions, actor, reason)
    except DatabaseOperationError as error:
        if error.state not in {"22023", "23514", "P0002"}:
            raise
        raise IntakeReviewError("intakeReview.stale") from None
    except (OSError, ValueError):
        raise IntakeReviewError("intakeReview.stale") from None

    claim_id = None
    if "target" in body:
        frozen = intake.target(store, UUID(str(body["target"]["id"])))
        claim_id = str(frozen["claim_id"]) if frozen.get("claim_id") is not None else None
    return {"id": str(intake_id), "goal_id": str(result["id"]), "claim_id": claim_id, "confirmed": True}
