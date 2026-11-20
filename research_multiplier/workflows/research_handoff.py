"""Preview and export a scoped read-only handoff from one research snapshot."""

from datetime import datetime, timezone
import errno
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote
from uuid import UUID

from research_multiplier.storage import paths
from research_multiplier.storage.artifacts import write_bytes
from research_multiplier.workflows import handoff_renderers, proof_graph, research_resume, research_review


def _selected(store, claims):
    if claims is None:
        return store.query("human", """
            SELECT c.id, c.title, c.statement, c.domain, s.status, c.contract_sha256
            FROM rm.claims c JOIN rm.claim_foundation_status s ON s.id = c.id
            WHERE c.kind = 'researcher_claim' AND s.status NOT IN ('superseded', 'archived')
            ORDER BY c.created_at, c.id
        """)
    rows = store.query("human", """
        SELECT c.id, c.title, c.statement, c.domain, s.status, c.contract_sha256
        FROM rm.claims c JOIN rm.claim_foundation_status s ON s.id = c.id
        WHERE c.kind = 'researcher_claim' AND c.id = ANY(%s::uuid[])
    """, list(claims))
    by_id = {row["id"]: row for row in rows}
    if any(claim not in by_id for claim in claims):
        raise ValueError("A selected claim is unavailable in this research workspace.")
    return [by_id[claim] for claim in claims]


def _sources(store, claim_id):
    rows = store.query("human", """
        SELECT id, body ->> 'identifier' AS identifier, body ->> 'title' AS title,
               body ->> 'locator' AS locator, source_sha256 AS sha256,
               CASE WHEN retired THEN 'withdrawn' WHEN confirmed_at IS NOT NULL THEN 'confirmed'
                    ELSE 'pending' END AS status
        FROM rm.literature_source_status WHERE claim_id = %s ORDER BY identifier, recorded_at, id
    """, claim_id)
    for source in rows:
        try:
            identifier = research_review.normalize_identifier(source["identifier"])
        except (AttributeError, ValueError):
            continue
        prefix, _, reference = identifier.partition(":")
        publisher = "https://doi.org/" if prefix == "doi" else "https://arxiv.org/abs/"
        source["url"] = publisher + quote(reference, safe="/")
    return rows


def _evidence(store, claim_id, adopted):
    rows = store.query("human", """
        SELECT e.id, e.kind, e.issued_at, e.review_status, e.needs_recheck, e.verdict, e.diff_status,
               e.verifier_name AS verifier, e.verifier_version, e.code_revision AS checker_revision,
               e.environment_sha256, e.issuance_sha256, e.artifact_sha256,
               c.body #> '{dependencies,external_constants}' AS external_declarations
        FROM rm.evidence_foundation_status e LEFT JOIN rm.lean_check_status c ON c.id = e.lean_check_id
        WHERE e.claim_id = %s ORDER BY e.issued_at DESC, e.id
    """, claim_id)
    for row in rows:
        row["status"] = ("needs_recheck" if row["needs_recheck"] else
                         "verified" if row["verdict"] == "verified" and row["diff_status"] == "exact" else
                         "provisional")
        row["adopted"] = str(row["id"]) == adopted
    return rows


def _notes(store, claim_id):
    # Human-authored text only. Provider transcripts, credentials and local file paths are never loaded.
    notes = store.query("human", """
        SELECT id, node_id, text, created_at AS recorded_at
        FROM rm.workbench_guidance WHERE claim_id = %s ORDER BY created_at, id
    """, claim_id)
    understanding = store.query("human", """
        SELECT u.id, u.evidence_id, u.explanation, u.lesson, u.recorded_at
        FROM rm.research_understanding u JOIN rm.evidence_foundation_status e ON e.id = u.evidence_id
        WHERE e.claim_id = %s ORDER BY u.recorded_at, u.id
    """, claim_id)
    return {"guidance": notes, "understanding": understanding}


def _decision(row):
    return {key: row[key] for key in ("kind", "question", "reference") if key in row}


def _section(store, row):
    identifier = row["id"]
    current = research_resume.snapshot(store, identifier, limit=research_resume.MAX_LIMIT)
    adopted = (current["conclusion"] or {}).get("reference", {}).get("evidence_id")
    graph = (proof_graph.snapshot(store, identifier) if row["contract_sha256"] else
             {"claim": row, "nodes": [], "edges": [], "blockers": []})
    decisions = research_review.inbox(store, identifier, limit=1000)
    return {
        "graph": graph,
        "reference": {**current["reference"], "contract_sha256": row["contract_sha256"]},
        "scope": {key: current["scope"][key] for key in ("frozen", "basis_reviewed", "hypotheses", "conclusion",
                                                        "formal_target", "lean_proposition")},
        "evidence": _evidence(store, identifier, str(adopted) if adopted else None),
        "decisions": [{"kind": decision["kind"], "question": decision["question"],
                       "reference": {"item_id": decision["item_id"]}} for decision in decisions],
        "decision_limit_reached": len(decisions) == 1000,
        "next": current["next"], "sources": _sources(store, identifier), "notes": _notes(store, identifier),
    }


def _project_queue(store):
    current = research_resume.snapshot(store, limit=research_resume.MAX_LIMIT)
    decisions = current["unassigned_decisions"]
    return {"unassigned_decisions": {**decisions, "items": [_decision(row) for row in decisions["items"]]},
            "targets_waiting_for_basis": current["targets_waiting_for_basis"],
            "frames_waiting_for_conjecture": current["frames_waiting_for_conjecture"]}


def capture(store, *, claims, language):
    """Take a reproducible, allowlisted snapshot of the explicitly selected scope."""
    root = paths.require_workspace()
    if language not in ("en", "zh-CN"):
        raise ValueError("Choose English (en) or Simplified Chinese (zh-CN).")
    if claims is not None:
        try:
            claims = tuple(dict.fromkeys(UUID(str(claim)) for claim in claims))
        except ValueError:
            raise ValueError("Supply a valid claim UUID for each selected claim.") from None
        if not claims:
            raise ValueError("Select a claim or the whole project.")

    def read():
        return {"format_version": 1, "workspace_id": str(paths.workspace_id(root)), "language": language,
                "scope": "project" if claims is None else "claims",
                "claims": [_section(store, row) for row in _selected(store, claims)],
                **({"project_queue": _project_queue(store)} if claims is None else {})}

    document = store.transaction("human", read, read_only=True)
    # Canonical values serve both the exact local preview and the two printable formats.
    encoded = json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return {"snapshot_sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(), "snapshot": json.loads(encoded)}


def handoff(store, *, claims, language="en", out=None, confirm=None):
    if (out is None) != (confirm is None):
        raise ValueError("Supply both --out and the --confirm digest shown by the handoff preview.")
    snapshot = capture(store, claims=claims, language=language)
    if out is None:
        return snapshot
    if confirm != snapshot["snapshot_sha256"]:
        raise ValueError("The handoff scope or snapshot has changed. Preview again before exporting.")
    selected = Path(out).expanduser()
    if selected.is_symlink():
        raise ValueError("Choose a new handoff directory, rather than a symbolic link.")
    target = paths.external_destination(selected, "exports")
    if target.exists() or target.is_symlink():
        raise FileExistsError(errno.EEXIST, "Choose a new handoff directory", str(target))
    if not target.parent.is_dir():
        raise ValueError("Choose an existing parent directory for the handoff.")
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    files = handoff_renderers.render(snapshot["snapshot"], captured_at=timestamp, digest=confirm, language=language)
    with TemporaryDirectory(dir=target.parent, prefix=".trellin-handoff-") as directory:
        staging = Path(directory) / "handoff"
        staging.mkdir(mode=0o700)
        for name, content in files.items():
            write_bytes(staging / name, content.encode("utf-8"), external=True)
        if target.exists() or target.is_symlink():
            raise FileExistsError(errno.EEXIST, "Choose a new handoff directory", str(target))
        staging.rename(target)
    return {"directory": str(target), "files": list(files), "captured_at": timestamp, "snapshot_sha256": confirm}
