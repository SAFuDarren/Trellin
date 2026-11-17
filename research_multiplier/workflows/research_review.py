"""Human review and original-source archives for frozen research claims."""

from concurrent.futures import Future
from datetime import datetime
import json
from pathlib import Path
import re
from threading import Thread
from urllib.error import URLError
from urllib.request import Request, build_opener

from research_multiplier.storage import paths
from ..policy import scope_review
from ..storage.artifacts import Artifacts
from ..models.model_http import NoRedirect
from research_multiplier.protocol.serialization import terminal_line

MAX_PDF_BYTES = 32 * 1024**2
DOWNLOAD_SECONDS = 30
ARXIV_ID = re.compile(r"(?:[0-9]{4}[.][0-9]{4,5}|[A-Za-z.-]+/[0-9]{7})v[1-9][0-9]*")
DOI_ID = re.compile(r"10[.][0-9]{4,9}/[^\s\x00-\x1f\x7f]+")


def inbox(store, claim_id=None, limit=50):
    """List questions and gates waiting for a researcher, most urgent first."""
    if not 1 <= limit <= 1000:
        raise ValueError("Review inbox limit must be between 1 and 1000.")
    return store.query("human", """
        WITH open_items AS (
            SELECT kind, item_id, claim_id, waiting_since, question, detail,
                   CASE WHEN kind IN ('resolve_clarification', 'decide_condition')
                       OR (kind = 'review_evidence' AND detail ->> 'kind' = 'counterexample') THEN 1 ELSE 2 END AS priority
            FROM rm.research_actionable_review_inbox
            UNION ALL
            SELECT 'checker_hold', id, NULL::uuid, recorded_at,
                   'Review the verification change before releasing or retiring this revision.',
                   jsonb_build_object('checker_revision', checker_revision, 'policy_id', policy_id), 0
            FROM rm.checker_hold_status WHERE released_at IS NULL
        )
        SELECT * FROM open_items
        WHERE %s::uuid IS NULL OR claim_id = %s
        ORDER BY priority, waiting_since, kind, item_id LIMIT %s
    """, claim_id, claim_id, limit)


def normalize_identifier(value):
    prefix, colon, suffix = value.strip().partition(":")
    if colon and prefix.lower() == "arxiv" and ARXIV_ID.fullmatch(suffix):
        return "arXiv:" + suffix
    if colon and prefix.lower() == "doi" and DOI_ID.fullmatch(suffix):
        return "doi:" + suffix.lower()
    raise ValueError("Use a versioned arXiv ID such as arXiv:2401.01234v1 or a DOI in the form doi:10.1234/name.")


def _read_pdf(stream):
    content = stream.read(MAX_PDF_BYTES + 1)
    if len(content) > MAX_PDF_BYTES or not content.startswith(b"%PDF-"):
        raise ValueError("The original must be a PDF no larger than 32 MiB.")
    return content


def _download_arxiv(identifier):
    if not identifier.startswith("arXiv:") or not ARXIV_ID.fullmatch(identifier[6:]):
        raise ValueError("Direct download requires a versioned arXiv identifier. Use import-source for a local DOI PDF.")
    request = Request("https://arxiv.org/pdf/" + identifier[6:],
                      headers={"Accept": "application/pdf", "User-Agent": "Research-Multiplier-Literature/1"})
    result = Future()

    def download():
        try:
            with build_opener(NoRedirect()).open(request, timeout=DOWNLOAD_SECONDS) as response:
                result.set_result(_read_pdf(response))
        except Exception as error:
            result.set_exception(error)

    Thread(target=download, daemon=True).start()
    try:
        return result.result(timeout=DOWNLOAD_SECONDS)
    except (TimeoutError, URLError):
        raise ValueError("The original could not be downloaded from arXiv. Import a saved copy instead.") from None


def archive_source(store, claim, identifier, metadata, pdf=None):
    identifier = normalize_identifier(identifier)
    if not isinstance(metadata, dict) or set(metadata) != {"title", "locator", "excerpt", "convention_map"}:
        raise ValueError("Source metadata requires title, locator, excerpt and convention_map.")
    if not all(isinstance(metadata[key], str) and metadata[key].strip() for key in ("title", "locator", "excerpt")):
        raise ValueError("A source needs a title, theorem locator and excerpt from the original.")
    if not isinstance(metadata["convention_map"], dict):
        raise ValueError("Use a JSON object to map source conventions to project conventions.")
    if not store.query("human", "SELECT id FROM rm.claims WHERE id = %s", claim):
        raise ValueError("No research claim matches this source registration.")
    if pdf is None:
        content = _download_arxiv(identifier)
        origin = "arxiv_download"
    else:
        with Path(pdf).open("rb") as stream:
            content = _read_pdf(stream)
        origin = "operator_import"
    artifacts = Artifacts(paths.object_store(), workspace=False)
    body = {**metadata, "identifier": identifier, "archive": artifacts.put_bytes(content), "origin": origin}
    return store.query("human", "SELECT * FROM rm.register_literature_source(%s,%s)",
                       claim, store.json(body))[0]


def archived_source(store, source, *, expected_sha=None):
    rows = store.query("human", "SELECT body, source_sha256 FROM rm.literature_source_status WHERE id = %s", source)
    if not rows or (expected_sha is not None and rows[0]["source_sha256"] != expected_sha):
        raise ValueError("No literature source matches that ID and version hash.")
    return Artifacts(paths.object_store(), workspace=False).read(rows[0]["body"]["archive"])


def confirm_source(store, source, digest, actor, reason):
    archived_source(store, source, expected_sha=digest)
    return store.query("human", "SELECT rm.confirm_literature_source(%s,%s,%s,%s) AS id",
                       source, digest, actor, reason)[0]


def _read_artifact(artifacts, row):
    if row is None or not row["artifact_uri"]:
        return None
    ref = {"uri": row["artifact_uri"], "sha256": row["artifact_sha256"]}
    try:
        body = artifacts.read_json(ref)
    except FileNotFoundError:
        return None
    if not isinstance(body, dict):
        raise ValueError("The archived research report has an unexpected format.")
    return body


def evidence_packet(store, evidence_id, run_directory=None):
    rows = store.query("human", """
        SELECT e.*, c.title, c.domain, c.contract_snapshot, effective.status AS claim_status,
               effective.recorded_status AS recorded_claim_status, effective.interpretation_hold
        FROM rm.evidence_foundation_status e
        JOIN rm.claims c ON (c.id, c.contract_sha256) = (e.claim_id, e.contract_sha256)
        JOIN rm.claim_foundation_status effective ON effective.id = c.id
        WHERE e.id = %s
    """, evidence_id)
    if not rows:
        raise ValueError("No evidence matches this review request.")
    row = rows[0]
    directories = Path(run_directory) if run_directory else paths.claim_runs() / str(row["claim_id"])
    artifacts = Artifacts(directories / "artifacts")
    conclusion = row["contract_snapshot"]
    check = None
    if row["lean_check_id"]:
        check, = store.query("human", "SELECT body FROM rm.lean_check_status WHERE id = %s", row["lean_check_id"])
    scoped_revision = check["body"].get("local_checker_revision") if check and row["verification_attempt_id"] else None
    attempts = store.query("human", """
        SELECT id, role, finding, artifact_uri, artifact_sha256, harness_version, model_provider, model_id,
               model_family, model_revision, prompt_sha256, input_tokens, output_tokens, cost_usd
        FROM rm.attempts
        WHERE id IN (%s, %s, %s)
            OR id IN (SELECT attempt_id FROM rm.lean_core_author_provenance WHERE evidence_id = %s)
        ORDER BY submitted_at, id
    """, row["attempt_id"], row["audit_attempt_id"], row["verification_attempt_id"], row["id"])
    search_traces = store.query("human", """
        SELECT trace_sha256, proposal_attempt_ids FROM rm.lean_search_provenance
        WHERE evidence_id = %s ORDER BY trace_sha256
    """, evidence_id)
    audit_attempt = next((a for a in attempts if a["id"] == row["audit_attempt_id"]), None)
    prove_attempt = next((a for a in attempts if a["id"] == row["attempt_id"]), None)
    proof = _read_artifact(artifacts, prove_attempt)
    audit = _read_artifact(artifacts, audit_attempt)
    needed = store.query("human", """
        SELECT kind, item_id, question, detail FROM rm.research_actionable_review_inbox
        WHERE claim_id = %s AND (kind <> 'review_evidence' OR item_id = %s)
        ORDER BY waiting_since, item_id
    """, row["claim_id"], evidence_id)
    if check:
        body = check["body"]
        steps = {key: body[key] for key in ("statement_type", "target", "theorem", "candidate_theorem",
                                             "basis_version_ids", "expanded_basis_version_ids",
                                             "local_lemma_ids", "expanded_local_lemma_ids",
                                             "direct_basis_version_ids", "source_module", "source_declaration",
                                             "expected_type", "actual_type", "lean_source", "lake_validation",
                                             "native_capsule", "native_replay", "origin")
                 if key in body}
        dependencies = body.get("dependencies", {})
        steps["axioms"] = dependencies.get("axioms", [])
        steps["direct_constants"] = dependencies.get("direct_constants", [])
        if "external_constants" in dependencies:
            steps["external_constants"] = dependencies["external_constants"]
        steps["premises"] = dependencies.get("premise_usage", [])
        if "implication_binder_occurs_in_proof" in dependencies:
            steps["implication_binder_occurs_in_proof"] = dependencies["implication_binder_occurs_in_proof"]
    elif proof and isinstance(proof.get("proof_markdown"), str):
        steps = {"recorded_explanation": proof["proof_markdown"], "basis_items": proof.get("basis_items", [])}
    else:
        steps = {}
    refutation = {}
    if row["kind"] == "counterexample":
        refutation = {"refutation": {
            "contract_sha256": row["contract_sha256"],
            "original_status": row["claim_status"],
            "mode": row["result"].get("refutation_mode", "exact_certificate"),
            "review_question": "Does this exact refutation reveal a problem with the intended assumptions?",
        }}
        if row["result"].get("refutation_mode") == "logical_negation":
            refutation["refutation"].update(negated_type=row["result"]["negated_type"], witness=None)
    return {
        "claim_id": row["claim_id"],
        **refutation,
        "title": row["title"],
        "domain": row["domain"],
        "claim_status": row["claim_status"],
        "recorded_claim_status": row["recorded_claim_status"],
        "interpretation_hold": row["interpretation_hold"],
        "evidence_id": row["id"],
        "contract_comparison": {
            "frozen_statement": conclusion["statement"],
            "frozen_hypotheses": conclusion["hypotheses"],
            "frozen_conclusion": conclusion["conclusion"],
            "scope_status": row["diff_status"],
            "verified_result": row["result"],
        },
        "verification": {
            "grade": row["kind"], "verdict": row["verdict"], "review_status": row["review_status"],
            "needs_recheck": row["needs_recheck"], "verifier": row["verifier_name"],
            "verifier_version": row["verifier_version"], "checker_revision": row["code_revision"],
            "effective_checker_revision": scoped_revision or row["code_revision"],
            "environment_sha256": row["environment_sha256"],
            "artifact": {"uri": row["artifact_uri"], "sha256": row["artifact_sha256"]},
            "attempts": attempts,
            "lean_search_traces": search_traces,
            "decisions": store.query("human", """
                SELECT action, decided_by, actor_ref, reason, decided_at FROM rm.decisions
                WHERE evidence_id = %s ORDER BY sequence_no
            """, evidence_id),
        },
        "key_steps": steps,
        "independent_audit": {
            "mode": row["audit_mode"], "attempt_id": row["audit_attempt_id"], "recorded_report": audit,
            "blind_scope_extractions": scope_review.extractions(store, evidence_id),
        },
        "questions": needed,
        "understanding": store.query("human", """
            SELECT actor_ref, explanation, lesson, recorded_at FROM rm.research_understanding
            WHERE evidence_id = %s ORDER BY recorded_at, id
        """, evidence_id),
    }

def explain_evidence(store, evidence_id, run_directory=None):
    """Summarize only checked scope and audited dependencies for researcher review."""
    packet = evidence_packet(store, evidence_id, run_directory)
    scope = packet["contract_comparison"]
    verification = packet["verification"]
    steps = packet["key_steps"]
    lines = [
        terminal_line(packet["title"]),
        f"Frozen question: {terminal_line(scope['frozen_statement'])}",
        f"Verification: {verification['verdict']} · scope {scope['scope_status']} · review {verification['review_status']}",
    ]
    if verification["needs_recheck"]:
        lines.append("Recorded evidence requires recheck before reuse or adoption.")
    if "refutation" in packet:
        lines.append(f"Refutation mode: {terminal_line(packet['refutation']['mode'])}")
    if steps.get("actual_type"):
        lines.append(f"Checked Lean proposition: {terminal_line(steps['actual_type'])}")

    uses = {premise["item"]: premise for premise in steps.get("premises", [])
            if isinstance(premise, dict) and "item" in premise}
    if scope["frozen_hypotheses"]:
        lines.append("Frozen premises")
        for premise in scope["frozen_hypotheses"]:
            use = uses.get(premise["label"], {})
            occurs = use.get("occurs_in_proof_term", use.get("in_proof"))
            use_status = {True: "observed in proof", False: "not observed in proof", None: "usage not reported"}[occurs]
            lines.append(f"  - {terminal_line(premise['label'])} {terminal_line(premise['statement'])} · {use_status}")

    versions = steps.get("expanded_basis_version_ids", steps.get("basis_version_ids", []))
    if versions:
        facts = store.query("human", """
            SELECT id, body ->> 'natural' AS statement FROM rm.formal_item_status
            WHERE id = ANY(%s::uuid[]) ORDER BY logical_key, version, id
        """, versions)
        lines.append("Recorded library dependencies")
        lines.extend(f"  - {terminal_line(fact['statement'])} ({fact['id']})" for fact in facts)
    local = steps.get("expanded_local_lemma_ids", steps.get("local_lemma_ids", []))
    if local:
        lines.append(f"Verified intermediate lemmas referenced: {len(local)}")
    if packet["questions"]:
        lines.append("Open review items")
        lines.extend(f"  - {terminal_line(item['question'])}" for item in packet["questions"])
    lines.append(f"Full record: research-multiplier review packet {evidence_id}")
    return "\n".join(lines)
