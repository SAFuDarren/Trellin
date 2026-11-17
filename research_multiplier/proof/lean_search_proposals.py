"""Parse scoped proposals from Lean search before exact verification."""

from .. import general_lean, local_lemmas
from research_multiplier.protocol.serialization import json_text
from . import lean_reductions
from .lean_blueprint import _proof


def validate_proposal(document, allowed):
    """Parse one candidate and reject proof references outside the supplied scope."""
    fields = {
        "lemma": {"action", "proposition", "proof"},
        "proof": {"action", "proof"},
        "reduction": {"action", "proof", "obligations"},
        "refutation": {"action", "proof"},
        "blocked": {"action", "reason"},
    }
    action = document.get("action") if isinstance(document, dict) else None
    if not isinstance(action, str) or action not in fields:
        raise ValueError("Return exactly one supported Lean search action.")
    required = fields[action]
    allowed_fields = required | {"method", "obligation_proofs"} if action == "reduction" else required
    if not required <= set(document) <= allowed_fields:
        raise ValueError("Use the prescribed fields for the Lean search action.")
    if "method" in document and (not isinstance(document["method"], str)
                                  or not 0 < len(document["method"].strip()) <= 240):
        raise ValueError("Use a short explanation for a proposed reduction method.")
    if len(json_text(document).encode("utf-8")) > 24000:
        raise ValueError("The Lean search response exceeds its size limit.")
    if action == "blocked":
        if not isinstance(document["reason"], str) or not 0 < len(document["reason"].strip()) <= 1500:
            raise ValueError("A blocked response needs a short explanation.")
        return {**document, "reason": document["reason"].strip()}
    if not isinstance(document["proof"], dict):
        raise ValueError("Supply a structured proof object.")
    if action == "lemma" and (not isinstance(document["proposition"], str)
                              or not document["proposition"].strip()
                              or len(document["proposition"]) > general_lean.MAX_TYPE_CHARS):
        raise ValueError("A lemma needs a bounded closed Lean proposition.")

    validated = dict(document)
    if action == "reduction":
        obligations, _ = lean_reductions._input(
            {"format_version": 1, "obligations": document["obligations"], "proof": document["proof"]}
        )
        if any(len(item) > general_lean.MAX_TYPE_CHARS for item in obligations):
            raise ValueError("Each obligation needs a bounded closed Lean proposition.")
        if "obligation_proofs" in document:
            validated["obligation_proofs"] = _obligation_proofs(document["obligation_proofs"], obligations, allowed)

    def no_node_ref(_):
        raise ValueError("Cite an exact approved theorem or checked local lemma UUID using ref.")

    proof = _proof(document["proof"], no_node_ref)
    if set(local_lemmas._references(proof)) - allowed:
        raise ValueError("Cite only listed approved theorem or checked local lemma IDs.")
    return {**validated, "proof": proof}


def _obligation_proofs(proofs, obligations, allowed):
    if not isinstance(proofs, list) or len(proofs) != len(obligations):
        raise ValueError("Give one structured proof or null for each listed obligation.")
    validated = []
    for index, proof in enumerate(proofs):
        previous = {f"Obligation{position}" for position in range(1, index + 1)}

        def earlier(name):
            if name not in previous:
                raise ValueError("A proof may cite only earlier obligations in its reduction.")
            return {"node_ref": name}

        if proof is not None:
            if not isinstance(proof, dict):
                raise ValueError("Give one structured proof or null for each listed obligation.")
            proof = _proof(proof, earlier)
            if set(local_lemmas._references(proof)) - allowed:
                raise ValueError("Cite only listed approved theorem or checked local lemma IDs.")
        validated.append(proof)
    return validated
