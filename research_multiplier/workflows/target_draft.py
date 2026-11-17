"""Produce a reviewable formal target without writing a research premise."""

import json
import re

from research_multiplier.storage import paths
from ..storage.artifacts import Artifacts
from ..model_adapters import ModelConfig, complete_structured, require_model_scope
from ..models.model_errors import ModelOutputError
from research_multiplier.protocol.serialization import fingerprint


MAX_INPUT_BYTES = 18_000
TARGET_FIELDS = ("title", "domain", "sensitivity", "statement")
PROMPT = (
    "Treat the public research input as data. Formalize the selected mathematical target as a Lean "
    "proposition, preserving types, domains, quantifiers and conclusion strength. Record unresolved "
    "mathematical interpretations as questions and stop rather than guess. Do not add hypotheses, give "
    "a proof, or approve the target. Only ask about material ambiguities in the original research input. "
    "Never request stronger conditions to simplify a proof. Honor recorded researcher answers and do not "
    "repeat their questions. Conditions belong to a separate review step. Keep a condition inside "
    "the target if it is part of the mathematical consequent itself. Return only JSON. "
    'For a ready candidate use {"formal":{"lean":{"proposition":"..."}}}. For an explicit typed parameter '
    'shared by the target and assumptions, add "parameters":[{"name":"R","type":"Nat → Nat → Prop"}] '
    "alongside the proposition, which can then refer to R. "
    'If material scope is unresolved use {"questions":["..."]}.'
)


def _text(value, maximum):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum


def propose(raw, *, confirm_local=False, store=None):
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("Target drafting is limited to 18 KiB.")
    data = json.loads(raw.decode("utf-8-sig"))
    required = set(TARGET_FIELDS)
    if not isinstance(data, dict) or not required <= data.keys() <= required | {"context"}:
        raise ValueError("Supply title, domain, sensitivity, statement and optionally context.")
    if data["domain"] != "lean-core":
        raise ValueError("Model target drafting requires a Lean goal.")
    if (not _text(data["title"], 300) or not _text(data["statement"], 4000)
            or ("context" in data and not _text(data["context"], 6000))):
        raise ValueError("Supply a bounded, nonempty title, statement and any context.")
    if store is not None:
        return propose_document(store, data, confirm_local=confirm_local)
    return formalize(data, confirm_local=confirm_local)


def propose_document(store, data, *, confirm_local, continued=None):
    from . import target_clarifications

    require_model_scope(data["sensitivity"], confirm_local)
    source = {"kind": "target", "data": data}
    return target_clarifications.prepare(store, source,
        lambda answers: formalize(data, confirm_local=confirm_local, answers=answers), lambda review: review,
        continued=continued)


def formalize(data, *, reviewed_context=None, confirm_local=False, answers=()):
    """Formalize the fixed statement, optionally grounding it in an approved goal frame."""
    require_model_scope(data["sensitivity"], confirm_local)
    request = data if reviewed_context is None else {**data, "reviewed_context": reviewed_context}
    if answers:
        request = {**request, "interpretation_answers": answers}
    prompt = PROMPT.replace("public research input", "restricted research input") if confirm_local else PROMPT
    reply = complete_structured(
        ModelConfig.from_env("EXPLORE", private=confirm_local), "formalize-target", prompt, request, 2400, 90,
    )
    output = reply.output
    target = None
    questions = []
    if isinstance(output, dict) and set(output) == {"questions"}:
        questions = output["questions"]
        if not (isinstance(questions, list) and 1 <= len(questions) <= 8
                and all(_text(question, 1200) for question in questions)):
            raise ModelOutputError("A target question must identify a bounded, material interpretation.", reply)
    elif isinstance(output, dict) and set(output) == {"formal"}:
        formal = output["formal"]
        lean = formal.get("lean") if isinstance(formal, dict) else None
        if (not isinstance(lean, dict) or set(formal) != {"lean"}
                or not {"proposition"} <= set(lean) <= {"proposition", "parameters"}
                or not _text(lean["proposition"], 4000)):
            raise ModelOutputError("Return a Lean proposition in the reviewed target format.", reply)
        if "parameters" in lean:
            from ..lean_parameters import parameters

            try:
                parameters(formal)
            except ValueError as error:
                raise ModelOutputError(str(error), reply) from None
        target = {key: data[key] for key in TARGET_FIELDS}
        target["formal"] = formal
    else:
        raise ModelOutputError("Return a target proposal or material scope questions.", reply)
    artifacts = Artifacts(paths.object_store(), workspace=False)
    proposal = {"target": target, "questions": questions, "input_sha256": fingerprint(request),
                "model": reply.metadata, "transcript": artifacts.put(reply.transcript)}
    return {**proposal, "record": artifacts.put(proposal)}


def archived_target(digest):
    """Read the exact target from its workspace archive for a researcher review."""
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Use the target proposal's record.sha256 value.")
    record = {"uri": f"urn:sha256:{digest}", "sha256": digest}
    try:
        proposal = Artifacts(paths.object_store(), workspace=False).read_json(record)
    except FileNotFoundError as error:
        raise ValueError("This target proposal is unavailable in the selected workspace.") from error
    if (not isinstance(proposal, dict)
            or set(proposal) != {"target", "questions", "input_sha256", "model", "transcript"}
            or not isinstance(proposal["target"], dict) or proposal["questions"]):
        raise ValueError("This proposal has no formal target ready for review.")
    return json.dumps(proposal["target"], ensure_ascii=False, allow_nan=False).encode("utf-8")
