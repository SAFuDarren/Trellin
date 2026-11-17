"""Prepare a prospective next Lean goal from an adopted proof and its scope."""

from .. import general_lean
from ..proof import lean_proof_search
from research_multiplier.protocol.serialization import fingerprint


def _source(store, claim_id):
    records = store.query("human", """
        SELECT c.title, c.sensitivity, c.contract_snapshot AS contract, c.decisive_evidence_id AS evidence_id,
               checked.packet_id, checked.body #> '{dependencies,premise_usage}' AS premise_usage
        FROM rm.claims c
        JOIN rm.claim_foundation_status status ON status.id = c.id
        JOIN rm.evidence_foundation_status evidence ON evidence.id = c.decisive_evidence_id
        JOIN rm.lean_check_status checked ON checked.id = evidence.lean_check_id
        WHERE c.id = %s AND c.domain = 'lean-core' AND status.status = 'proved'
          AND evidence.verdict = 'verified' AND evidence.diff_status = 'exact'
          AND evidence.review_status = 'approved' AND NOT evidence.needs_recheck AND checked.check_ready
    """, claim_id)
    if not records:
        raise ValueError("Choose a claim with a current, adopted exact Lean proof.")
    return records[0]


def _choices(source):
    frozen = source["contract"]
    target, premises = general_lean._target(frozen)
    checked = {row["item"]: row for row in source["premise_usage"] or [] if isinstance(row, dict) and "item" in row}
    variants = []
    for hypothesis, proposition in zip(frozen["hypotheses"], premises):
        label = hypothesis["label"]
        use = checked.get(label, {})
        if use.get("proposition", proposition) != proposition:
            continue
        observed = use.get("occurs_in_proof_term", use.get("in_proof"))
        if observed is False:
            variants.append({
                "kind": "test_weaker_basis", "statement": source["contract"]["conclusion"]["statement"],
                "proposition": target, "omit_label": label,
                "reason": f"The adopted proof term does not use the {label} binder. Check the goal again without it.",
            })
    structure = lean_proof_search.conjunction(target, len(premises))
    if structure:
        variants.extend({
            "kind": "separate_consequence", "statement": proposition, "proposition": proposition,
            "reason": "Check this part of the adopted conjunction as a reusable result.",
        } for proposition in structure["obligations"])
    return [{**item, "sha256": fingerprint({"evidence": str(source["evidence_id"]), "candidate": item}),
             "status": "candidate_requires_review"} for item in variants[:12]]


def prepare(store, claim_id, selection=None):
    def make():
        source = _source(store, claim_id)
        candidates = _choices(source)
        if selection is None:
            return {"claim_id": str(claim_id), "evidence_id": source["evidence_id"], "candidates": candidates}
        match = next((item for item in candidates if item["sha256"] == selection), None)
        if match is None:
            raise ValueError("Choose an exact follow-up hash from the current adopted proof.")

        basis = store.query("human", "SELECT * FROM rm.get_packet_basis(%s)", source["packet_id"])
        omit = match.get("omit_label")
        if omit is not None and not any(
                item["packet_label"] == omit and item["body"]["kind"] == "hypothesis" for item in basis):
            raise ValueError("The premise to omit must match a current approved packet item.")
        retained = [str(item["item_version_id"]) for item in basis
                    if item["packet_label"] is not None and item["packet_label"] != omit
                    and item["body"]["kind"] in {"definition", "hypothesis", "theorem"}]
        draft = {
            "title": f"Follow-up: {source['title']}", "domain": "lean-core", "sensitivity": source["sensitivity"],
            "objective": match["reason"], "basis_version_ids": retained,
            "follow_up": {"source_claim_id": str(claim_id), "source_evidence_id": str(source["evidence_id"]),
                          "candidate": match},
        }
        goal, = store.query("human", "SELECT * FROM rm.create_research_goal(%s)", store.json(draft))
        return {**goal, "next_command": f"research-multiplier goal confirm {goal['id']} --hash {goal['frame_sha256']} "
                                        "--actor RESEARCHER --reason REASON"}

    return store.transaction("human", make) if selection is not None else make()


def propose(store, frame_id):
    frames = store.query("human", "SELECT body FROM rm.research_goal_status WHERE id = %s", frame_id)
    followup = frames[0]["body"].get("follow_up") if frames else None
    if not isinstance(followup, dict):
        raise ValueError("Choose a confirmed frame prepared by research follow-up.")
    source = _source(store, followup["source_claim_id"])
    match = followup["candidate"]
    if match not in _choices(source):
        raise ValueError("The proof supporting this follow-up changed. Review the current options.")
    proposal = {
        "statement": match["statement"], "formal": {"lean": {"proposition": match["proposition"]}},
        "provenance": {"kind": "program", "source": "checked-proof-follow-up",
                       "source_claim_id": followup["source_claim_id"],
                       "source_evidence_id": followup["source_evidence_id"], "candidate_sha256": match["sha256"]},
    }
    saved, = store.query("human", "SELECT * FROM rm.propose_research_conjecture(%s,%s)", frame_id, store.json(proposal))
    return {**saved, "next_command": f"research-multiplier goal select {saved['id']} --hash {saved['conjecture_sha256']} "
                                     "--actor RESEARCHER --reason REASON"}
