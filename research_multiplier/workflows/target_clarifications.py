"""Preserve material questions and precise human answers from target drafting."""

from contextlib import contextmanager
from datetime import datetime
import json
from uuid import UUID

from research_multiplier.storage import paths
from ..storage.artifacts import Artifacts
from research_multiplier.protocol.serialization import fingerprint


def _objects():
    return Artifacts(paths.object_store(), workspace=False)


def _read(checksum):
    return _objects().read_json({"uri": f"urn:sha256:{checksum}", "sha256": checksum})


def _encoded(value):
    if isinstance(value, (UUID, datetime)):
        return str(value)
    raise TypeError(f"Unexpected value in the clarification result: {type(value).__name__}")


def _row(store, identifier):
    rows = store.query("human", "SELECT * FROM rm.target_clarification_status WHERE id = %s", identifier)
    if not rows:
        raise ValueError("No formalization clarification matches this ID.")
    return rows[0]


def show(store, identifier):
    row = _row(store, identifier)
    fields = ("id", "title", "goal_id", "status", "questions", "answers", "actor_ref", "reason", "requested_at",
              "decided_at", "proposal_sha256", "result_sha256", "next_clarification_id")
    result = {key: row[key] for key in fields}
    result["statement"] = _read(row["subject_sha256"])["data"]["statement"]
    if row["status"] == "answered":
        flag = "--confirm-local" if row["sensitivity"] == "restricted" else "--confirm-public"
        result["continue_command"] = f"research-multiplier research clarify {identifier} --continue {flag}"
    if row["result_sha256"]:
        result["result"] = _read(row["result_sha256"])
    return result


def answer(store, identifier, answers, actor, reason):
    if answers is not None and not isinstance(answers, list):
        raise ValueError("Supply the answers as a JSON array of strings in question order.")
    store.query("human", "SELECT rm.answer_target_clarification(%s,%s,%s,%s)",
                identifier, store.json(answers) if answers is not None else None, actor, reason)
    return show(store, identifier)


def _history(store, row):
    history = store.query("human", """
        WITH RECURSIVE parents AS (
            SELECT id, parent_id, questions, answers, 0 AS depth
            FROM rm.target_clarification_status WHERE id = %s
            UNION ALL SELECT q.id, q.parent_id, q.questions, q.answers, p.depth + 1
                FROM rm.target_clarification_status q JOIN parents p ON p.parent_id = q.id
        )
        SELECT questions, answers FROM parents ORDER BY depth DESC
    """, row["id"])
    return [{"question": question, "answer": answer}
            for step in history for question, answer in zip(step["questions"], step["answers"])]


def prepare(store, source, draft, finish, *, continued=None):
    """Run the existing formalizer, saving only cases that need a human interpretation."""
    digest = fingerprint(source)
    if continued is None:
        saved = store.query("human", """
            SELECT id, proposal_sha256, result_sha256 FROM rm.target_clarification_status
            WHERE subject_sha256 = %s ORDER BY requested_at DESC, id LIMIT 1
        """, digest)
        if saved:
            [row] = saved
            return {**_read(row["result_sha256"] or row["proposal_sha256"]),
                    "clarification": show(store, row["id"])}
        answers = []
    else:
        if continued["subject_sha256"] != digest:
            raise ValueError("The formalization request changed since its questions were recorded.")
        answers = _history(store, continued)
    review = draft(answers)

    def record():
        result = finish(review)
        if review["questions"]:
            known = {" ".join(item["question"].casefold().split()).rstrip("?") for item in answers}
            questions = list(dict.fromkeys(question for question in review["questions"]
                             if " ".join(question.casefold().split()).rstrip("?") not in known))
            if not questions:
                raise ValueError("The formalizer repeated previously answered questions. The recorded answers remain available.")
            _objects().put(source)
            artifact = _objects().put(json.loads(json.dumps(result, default=_encoded)))
            identifier = store.query("human", "SELECT rm.record_target_clarification(%s,%s,%s,%s,%s,%s,%s,%s) AS id",
                digest, continued["id"] if continued else None, source.get("goal_id"), source.get("frame_sha256"),
                source["data"]["title"], source["data"]["sensitivity"], store.json(questions), artifact["sha256"])[0]["id"]
            row = _row(store, identifier)
            return {**_read(row["proposal_sha256"]), "clarification": show(store, identifier)}
        if continued is not None:
            artifact = _objects().put(json.loads(json.dumps(result, default=_encoded)))
            store.query("human", "SELECT rm.complete_target_clarification(%s,%s)", continued["id"], artifact["sha256"])
        return result

    return store.transaction("human", record)


@contextmanager
def _dispatch_lock(store, identifier):
    sql = "SELECT pg_try_advisory_lock(hashtextextended(%s, 4871)) AS acquired"
    if not store.query("human", sql, str(identifier))[0]["acquired"]:
        raise ValueError("This formalization is already continuing in another process.")
    try:
        yield
    finally:
        store.query("human", "SELECT pg_advisory_unlock(hashtextextended(%s, 4871))", str(identifier))


def continue_formalization(store, identifier, *, confirm_local):
    from . import goal_suggestions, target_draft
    from ..model_adapters import require_model_scope

    with _dispatch_lock(store, identifier):
        row = _row(store, identifier)
        if row["result_sha256"]:
            return _read(row["result_sha256"])
        if row["next_clarification_id"]:
            return {"clarification": show(store, row["next_clarification_id"])}
        if row["status"] != "answered":
            raise ValueError("Continue a current formalization after recording the requested answers.")
        source = _read(row["subject_sha256"])
        require_model_scope(source["data"]["sensitivity"], confirm_local)
        if source["kind"] == "target":
            return target_draft.propose_document(store, source["data"], confirm_local=confirm_local, continued=row)
        return goal_suggestions.formalize_discussion(store, source["goal_id"], source["discussion_sha256"],
                                                    source["candidate_index"], confirm_local=confirm_local,
                                                    continued=row)
