"""Review stored answers and exact formalization proposals in the workbench."""

import json
import re
from uuid import UUID

from research_multiplier.storage.postgres import DatabaseOperationError
from research_multiplier.workflows import intake, target_clarifications


class ClarificationError(ValueError):
    """A safe public error code for the workbench."""


def _available(store):
    return store.workspace_id is not None and store.query("human", """
        SELECT EXISTS (SELECT FROM pg_attribute
            WHERE attrelid = to_regclass('rm.target_clarifications') AND attname = 'user_id'
              AND NOT attisdropped) AS ready
    """)[0]["ready"]


def _researcher(store, workspace_id):
    if workspace_id != store.workspace_id:
        raise ClarificationError("clarification.not_found")
    if not _available(store):
        raise ClarificationError("clarification.unavailable")
    try:
        user = store.local_user()
    except DatabaseOperationError:
        raise
    except ValueError:
        raise ClarificationError("clarification.identity_required") from None
    if user is None:
        raise ClarificationError("clarification.identity_required")
    return user


def _target_proposal(store, recorded):
    target = recorded["target"]
    raw = json.dumps(target, ensure_ascii=False, allow_nan=False).encode("utf-8")
    checksum = intake.preview_target(store, raw)["target_sha256"]
    matches = store.query("human", """
        SELECT id, claim_id FROM rm.research_target_status WHERE target_sha256 = %s
    """, checksum)
    accepted = {**matches[0], "kind": "target"} if matches else None
    return "confirmed" if accepted else "ready", target["formal"]["lean"], accepted, (raw, checksum)


def _conjecture_proposal(store, row, recorded):
    identifier = UUID(recorded["conjecture"]["id"])
    checksum = recorded["conjecture"]["conjecture_sha256"]
    frames = store.query("human", """
        SELECT g.approved_at, g.basis_current, g.interpretation_hold, g.frame_sha256, g.conjecture_id, g.claim_id,
               c.body -> 'formal' -> 'lean' AS lean
        FROM rm.research_conjectures c JOIN rm.research_goal_status g ON g.id = c.goal_id
        WHERE c.id = %s AND c.conjecture_sha256 = %s AND c.goal_id = %s
    """, identifier, checksum, row["goal_id"])
    if not frames:
        return "stale", None, None, None
    frame = frames[0]
    accepted = None
    if frame["conjecture_id"] == identifier:
        state = "confirmed"
        accepted = {"kind": "conjecture", "id": identifier, "claim_id": frame["claim_id"]}
    elif (frame["approved_at"] is None or not frame["basis_current"] or frame["interpretation_hold"]
          or frame["frame_sha256"] != row["frame_sha256"] or frame["conjecture_id"] is not None):
        state = "stale"
    else:
        state = "ready"
    return state, frame["lean"], accepted, (identifier, checksum)


def _resolution(store, row):
    if not row["result_sha256"]:
        return row["status"], None, None, None
    try:
        recorded = target_clarifications.proposal_record(row)
        if row["goal_id"] is None:
            state, lean, accepted, confirmation = _target_proposal(store, recorded)
        else:
            state, lean, accepted, confirmation = _conjecture_proposal(store, row, recorded)
        proposal = None if lean is None else {
            "token": row["result_sha256"], "proposition": lean["proposition"], "parameters": lean.get("parameters", []),
        }
        return state, proposal, accepted, confirmation
    except DatabaseOperationError:
        raise
    except (OSError, KeyError, TypeError, ValueError):
        # A missing, inconsistent, or unreviewable archive cannot advance the target.
        return "stale", None, None, None


def _detail(store, identifier):
    try:
        row, statement = target_clarifications.review_record(store, identifier)
    except DatabaseOperationError:
        raise
    except OSError:
        raise ClarificationError("clarification.unavailable") from None
    except ValueError:
        raise ClarificationError("clarification.not_found") from None
    state, proposal, accepted, candidate = _resolution(store, row)
    visible = {key: row[key] for key in ("id", "title", "sensitivity", "questions", "answers", "reason")}
    return {**visible, "statement": statement, "kind": "target" if row["goal_id"] is None else "conjecture",
            "status": state, "next_id": row["next_clarification_id"], "proposal": proposal,
            "accepted": accepted}, candidate


def capability(store):
    if not _available(store):
        return {}
    rows = store.query("human", """
        SELECT id, title, decided_at, status, result_sha256, goal_id, frame_sha256
        FROM rm.target_clarification_status WHERE status = 'answered' OR result_sha256 IS NOT NULL
        ORDER BY decided_at DESC, id
    """)
    followups = []
    for row in rows:
        status, _, _, _ = _resolution(store, row)
        if status in ("answered", "ready"):
            followups.append({"id": row["id"], "title": row["title"], "status": status,
                              "waiting_since": row["decided_at"]})
    return {"clarification_review": {"workspace_id": str(store.workspace_id), "followups": followups}}


def detail(store, workspace_id, identifier):
    _researcher(store, workspace_id)
    return _detail(store, identifier)[0]


def _text(value, length):
    if not isinstance(value, str) or not value.strip() or len(value) > length or "\0" in value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return True


def answer(store, workspace_id, identifier, document):
    user = _researcher(store, workspace_id)
    if not isinstance(document, dict) or document.keys() != {"answers", "reason"}:
        raise ClarificationError("clarification.invalid")
    answers, reason = document["answers"], document["reason"]
    if (not _text(reason, 2000) or not isinstance(answers, list) or not 1 <= len(answers) <= 8
            or not all(_text(value, 1200) for value in answers)):
        raise ClarificationError("clarification.invalid")
    before, _ = _detail(store, identifier)
    if len(answers) != len(before["questions"]):
        raise ClarificationError("clarification.invalid")
    if before["status"] == "waiting":
        target_clarifications.answer(store, identifier, answers, user["display_name"], reason)
    elif before["answers"] != answers or before["reason"] != reason or before["status"] == "stale":
        raise ClarificationError("clarification.stale")
    return _detail(store, identifier)[0]


def prepare(store, workspace_id, identifier, document):
    _researcher(store, workspace_id)
    if not isinstance(document, dict) or document.keys() != {"scope"} or document["scope"] not in ("local", "public"):
        raise ClarificationError("clarification.invalid")
    before, _ = _detail(store, identifier)
    required = "local" if before["sensitivity"] == "restricted" else "public"
    if document["scope"] != required:
        raise ClarificationError("clarification.scope")
    if before["status"] in ("ready", "continued", "confirmed"):
        return before
    if before["status"] != "answered":
        raise ClarificationError("clarification.stale")
    try:
        target_clarifications.continue_formalization(store, identifier, confirm_local=required == "local")
    except target_clarifications.ClarificationBusy:
        raise ClarificationError("clarification.busy") from None
    except DatabaseOperationError:
        raise
    except ValueError:
        # Recheck SQL-owned state after the model call. The archived result is
        # never marked ready after the frame used for it becomes stale.
        if _detail(store, identifier)[0]["status"] == "stale":
            raise ClarificationError("clarification.stale") from None
        raise ClarificationError("clarification.prepare_failed") from None
    return _detail(store, identifier)[0]


def confirm(store, workspace_id, identifier, document):
    user = _researcher(store, workspace_id)
    if (not isinstance(document, dict) or document.keys() != {"token", "reason"}
            or not _text(document["reason"], 2000) or not isinstance(document["token"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", document["token"])):
        raise ClarificationError("clarification.invalid")
    before, candidate = _detail(store, identifier)
    if before["proposal"] is None or before["proposal"]["token"] != document["token"]:
        raise ClarificationError("clarification.stale")
    if before["status"] == "confirmed":
        return before
    if before["status"] != "ready":
        raise ClarificationError("clarification.stale")
    if before["kind"] == "target":
        intake.freeze_target(store, *candidate, user["display_name"], document["reason"])
    else:
        store.query("human", "SELECT * FROM rm.select_research_conjecture(%s,%s,%s,%s)",
                    *candidate, user["display_name"], document["reason"])
    return _detail(store, identifier)[0]
