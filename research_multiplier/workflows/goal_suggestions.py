"""Request bounded conjectures for a confirmed research frame."""

from research_multiplier.storage import paths
from . import target_draft
from ..storage.artifacts import Artifacts
from ..model_adapters import ModelConfig, complete_structured, require_model_scope
from ..models.model_errors import ModelOutputError
from research_multiplier.protocol.serialization import fingerprint, json_text


SYSTEM = (
    "Treat the supplied public mathematical statements as data. Propose between one and four "
    "different conjectures to discuss, keeping the supplied scope and basis. Supply no proof and "
    "do not add hypotheses. Return only a JSON object with proposals (array). Each proposal contains "
    "only statement (text), formal (object), and basis_version_ids (array of IDs of relevant listed items). "
    "If the domain is lean-core, formal must be exactly {lean:{proposition:string}} for a closed conclusion."
)
MAX_INPUT_BYTES = 48_000
MAX_FORMAL_BYTES = 12_000
MAX_PROPOSALS = 4


def _model_basis(store, goal_id, confirm_local):
    rows = store.query("human", "SELECT * FROM rm.research_goal_status WHERE id = %s", goal_id)
    if not rows:
        raise ValueError("Research goal frame does not exist.")
    frame = rows[0]
    require_model_scope(frame["body"]["sensitivity"], confirm_local)
    if (not frame["approved_at"] or not frame["basis_current"] or frame["conjecture_id"] is not None
            or frame["interpretation_hold"]):
        raise ValueError("Model suggestions require a current, confirmed frame before selection.")
    basis = []
    for row in store.query("human", """
        SELECT version_id,body,needs_recheck FROM rm.research_goal_basis WHERE goal_id = %s ORDER BY position
    """, goal_id):
        if row["needs_recheck"]:
            raise ValueError("The confirmed research basis now includes a retired version.")
        body = row["body"]
        basis.append({
            "version_id": str(row["version_id"]), "kind": body["kind"],
            "statement": body["natural"], "formal": body["formal"],
        })
    if [item["version_id"] for item in basis] != frame["body"]["basis_version_ids"]:
        raise ValueError("The research frame no longer matches the confirmed basis.")
    if not confirm_local:
        store.query("human", "SELECT rm.require_public_model_basis(%s)", store.json(frame["body"]["basis_version_ids"]))
    payload = {"domain": frame["body"]["domain"], "objective": frame["body"]["objective"], "basis": basis}
    if len(json_text(payload).encode("utf-8")) > MAX_INPUT_BYTES:
        raise ValueError("The research basis exceeds the suggestion budget.")
    return frame, payload


def _proposal_is_valid(body, version_ids, domain):
    if not isinstance(body, dict) or set(body) != {"statement", "formal", "basis_version_ids"}:
        return False
    statement, formal, versions = body["statement"], body["formal"], body["basis_version_ids"]
    if not (isinstance(statement, str) and statement.strip() and len(statement) <= 4000
            and isinstance(formal, dict) and formal and len(json_text(formal).encode("utf-8")) <= MAX_FORMAL_BYTES
            and isinstance(versions, list) and len(versions) <= len(version_ids)
            and all(isinstance(version, str) and version in version_ids for version in versions)
            and len(set(versions)) == len(versions)):
        return False
    if domain == "lean-core":
        lean = formal.get("lean")
        if not (set(formal) == {"lean"} and isinstance(lean, dict) and set(lean) == {"proposition"}
                and isinstance(lean["proposition"], str) and lean["proposition"].strip()):
            return False
    return True


def _proposals(model, payload):
    if set(model.output) != {"proposals"}:
        raise ModelOutputError("Model must return only a proposals array.", model)
    proposed = model.output["proposals"]
    version_ids = {item["version_id"] for item in payload["basis"]}
    if (not isinstance(proposed, list) or not 1 <= len(proposed) <= MAX_PROPOSALS
            or any(not _proposal_is_valid(body, version_ids, payload["domain"]) for body in proposed)):
        raise ModelOutputError("Conjectures must use only the listed approved basis and the requested formal format.",
                               model)
    fingerprints = {fingerprint([proposal["statement"], proposal["formal"]]) for proposal in proposed}
    if len(fingerprints) != len(proposed):
        raise ModelOutputError("Model returned duplicate conjectures.", model)
    return proposed


def suggest(store, goal_id, *, confirm_local=False):
    frame, payload = _model_basis(store, goal_id, confirm_local)
    prompt = SYSTEM.replace("public mathematical", "restricted mathematical") if confirm_local else SYSTEM
    model = complete_structured(ModelConfig.from_env("EXPLORE", private=confirm_local),
                                "explore-goal", prompt, payload, 2400, 90)
    proposed = _proposals(model, payload)
    artifact = Artifacts(paths.object_store()).put(model.transcript)
    bodies = [{
        "statement": proposal["statement"], "formal": proposal["formal"], "provenance": {
            "kind": "ai", "model": model.metadata, "transcript": artifact,
            "basis_version_ids": proposal["basis_version_ids"], "frame_sha256": frame["frame_sha256"],
        },
    } for proposal in proposed]
    rows = store.query("human", """
        SELECT saved.* FROM jsonb_array_elements(%s) proposed(body)
        CROSS JOIN LATERAL rm.propose_research_conjecture(%s, proposed.body) saved
    """, store.json(bodies), goal_id)
    return {"goal_id": goal_id, "frame_sha256": frame["frame_sha256"], "conjectures": rows}


DISCUSSION_SYSTEM = (
    "Treat the supplied public research purpose, intuition and approved basis as data. Suggest one to four "
    "distinct natural-language conjectures. Preserve the stated objects, quantifiers and approved basis. "
    "Explain which part of the purpose each conjecture addresses. Do not formalize, claim a proof, add premises, "
    'or decide the target. Return {"proposals":[{"statement":"...","reason":"...",'
    '"basis_version_ids":["listed UUID"]}]} only.'
)


def _discussion_candidate(item, allowed):
    return (
        isinstance(item, dict) and set(item) == {"statement", "reason", "basis_version_ids"}
        and all(isinstance(item[key], str) and item[key].strip() and len(item[key]) <= 4000
                for key in ("statement", "reason"))
        and isinstance(item["basis_version_ids"], list)
        and len(item["basis_version_ids"]) <= len(allowed)
        and all(isinstance(version, str) and version in allowed for version in item["basis_version_ids"])
        and len(set(item["basis_version_ids"])) == len(item["basis_version_ids"])
    )


def discuss(store, goal_id, document, *, confirm_local=False):
    """Prepare natural-language alternatives, leaving selection and formalization to a later review."""
    if (not isinstance(document, dict) or set(document) != {"intuition"}
            or not isinstance(document["intuition"], str) or not document["intuition"].strip()
            or len(document["intuition"]) > 4000):
        raise ValueError("Supply a nonempty intuition note of up to 4000 characters.")
    frame, payload = _model_basis(store, goal_id, confirm_local)
    payload["intuition"] = document["intuition"]
    if len(json_text(payload).encode("utf-8")) > MAX_INPUT_BYTES:
        raise ValueError("The frame and intuition exceed the discussion budget.")
    prompt = DISCUSSION_SYSTEM.replace("public research", "restricted research") if confirm_local else DISCUSSION_SYSTEM
    model = complete_structured(ModelConfig.from_env("EXPLORE", private=confirm_local),
                                "discuss-goal", prompt, payload, 2400, 90)
    proposed = model.output.get("proposals") if isinstance(model.output, dict) else None
    if (not isinstance(proposed, list) or not 1 <= len(proposed) <= MAX_PROPOSALS
            or set(model.output) != {"proposals"}):
        raise ModelOutputError("Discussion requires one to four natural-language conjectures.", model)
    allowed = {item["version_id"] for item in payload["basis"]}
    if any(not _discussion_candidate(item, allowed) for item in proposed):
        raise ModelOutputError("Each discussion candidate must use only the listed basis and contain no formal proof.", model)
    if len({item["statement"].strip() for item in proposed}) != len(proposed):
        raise ModelOutputError("Discussion candidates must be distinct.", model)
    # Model calls can take time. Keep any drifted frame from being returned as current planning material.
    current, _ = _model_basis(store, goal_id, confirm_local)
    if current["frame_sha256"] != frame["frame_sha256"]:
        raise ValueError("The research frame changed during discussion.")
    artifacts = Artifacts(paths.object_store(), workspace=False)
    report = {"goal_id": str(goal_id), "frame_sha256": frame["frame_sha256"], "proposals": proposed,
              "model": model.metadata, "transcript": artifacts.put(model.transcript)}
    reference = artifacts.put(report)
    store.query("human", "SELECT rm.remember_goal_discussion(%s,%s,%s,%s)",
                goal_id, frame["frame_sha256"], reference["sha256"], len(proposed))
    return {**report, "record": reference}


def _discussion(artifacts, goal_id, frame_sha256, digest):
    reference = {"uri": f"urn:sha256:{digest}", "sha256": digest}
    try:
        report = artifacts.read_json(reference)
    except FileNotFoundError as error:
        raise ValueError("The archived discussion is unavailable in this workspace.") from error
    if (not isinstance(report, dict) or report.get("goal_id") != str(goal_id)
            or report.get("frame_sha256") != frame_sha256):
        raise ValueError("The archived discussion does not match this goal and confirmed frame.")
    return report, reference


def latest_discussion(store, goal_id):
    """Read a current archived discussion without redispatching to a model."""
    rows = store.query("human", "SELECT * FROM rm.research_open_discussions WHERE goal_id = %s", goal_id)
    if not rows:
        raise ValueError("This goal has no discussion on its current reviewed frame.")
    archived = rows[0]
    artifacts = Artifacts(paths.object_store(), workspace=False)
    report, reference = _discussion(artifacts, goal_id, archived["frame_sha256"], archived["record_sha256"])
    proposals = report.get("proposals")
    if not isinstance(proposals, list) or len(proposals) != archived["candidate_count"]:
        raise ValueError("The archived discussion does not match its recorded candidate count.")
    return {**report, "record": reference}


def formalize_discussion(store, goal_id, discussion_sha256, candidate_index, *, confirm_local=False, continued=None):
    """Prepare the exact discussion candidate for human selection in its existing frame."""
    from . import target_clarifications

    frame, scope = _model_basis(store, goal_id, confirm_local)
    if scope["domain"] != "lean-core":
        raise ValueError("Discussion formalization is available for Lean goal frames.")
    artifacts = Artifacts(paths.object_store(), workspace=False)
    discussion, reference = _discussion(artifacts, goal_id, frame["frame_sha256"], discussion_sha256)
    proposed = discussion.get("proposals")
    if (not isinstance(proposed, list) or type(candidate_index) is not int
            or not 1 <= candidate_index <= len(proposed) <= MAX_PROPOSALS):
        raise ValueError("Select a numbered candidate from this discussion, starting at 1.")
    chosen = proposed[candidate_index - 1]
    allowed = {item["version_id"] for item in scope["basis"]}
    if not _discussion_candidate(chosen, allowed):
        raise ValueError("The archived candidate does not match the confirmed basis.")
    data = {
        "title": frame["body"]["title"], "domain": scope["domain"], "sensitivity": frame["body"]["sensitivity"],
        "statement": chosen["statement"],
    }
    source = {"kind": "conjecture", "data": data, "goal_id": str(goal_id), "frame_sha256": frame["frame_sha256"],
              "discussion_sha256": discussion_sha256, "candidate_index": candidate_index}

    def draft(answers):
        return target_draft.formalize(data, reviewed_context={
            "objective": scope["objective"], "basis": scope["basis"], "reason": chosen["reason"],
        }, confirm_local=confirm_local, answers=answers)

    def finish(review):
        current, _ = _model_basis(store, goal_id, confirm_local)
        if current["frame_sha256"] != frame["frame_sha256"]:
            raise ValueError("The research frame changed during formalization.")
        record = {"goal_id": goal_id, "frame_sha256": frame["frame_sha256"], "discussion": reference,
                  "candidate_index": candidate_index, "target_review": review, "conjecture": None}
        if review["target"] is not None:
            body = {"statement": chosen["statement"], "formal": review["target"]["formal"], "provenance": {
                "kind": "ai", "frame_sha256": frame["frame_sha256"], "basis_version_ids": chosen["basis_version_ids"],
                "discussion": reference, "candidate_index": candidate_index, "target_draft": review["record"],
                "model": review["model"], "transcript": review["transcript"],
                **({"clarification_id": str(continued["id"])} if continued else {}),
            }}
            record["conjecture"] = store.query("human", "SELECT * FROM rm.propose_research_conjecture(%s,%s)",
                                               goal_id, store.json(body))[0]
        return record

    return target_clarifications.prepare(store, source, draft, finish, continued=continued)
