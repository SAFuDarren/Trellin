"""Review work awaiting a researcher and check the provenance of references."""

from datetime import datetime
import json
from pathlib import Path
from uuid import UUID

from research_multiplier.workflows import literature, research_review
from research_multiplier.policy import review_disclosure
from research_multiplier.storage.postgres import Store
from research_multiplier.protocol.serialization import json_text


from ._command import register, ALL, Output
from ._output import json_default as _encode


def _identity(action):
    action.add_argument("--actor", required=True)
    action.add_argument("--reason", required=True)


def add_commands(commands):
    review = commands.add_parser("review", help="Read the review inbox and record literature judgments.")
    review.set_defaults(_handler=run)
    actions = review.add_subparsers(dest="review_action", required=True)

    inbox = register(actions, "inbox", help="List questions and gates waiting for a researcher.")
    inbox.add_argument("--claim", type=UUID)
    inbox.add_argument("--limit", type=int, default=50)

    policy = register(actions, "policy", help="Set review capacity and the proof explanation requirement.")
    policy.add_argument("--max-pending", type=int, help="Pause new automatic work at this many pending receipts.")
    policy.add_argument("--weekly-minutes", type=int, help="Pause after this many logged review minutes in seven days.")
    policy.add_argument("--require-understanding", action="store_true")
    _identity(policy)
    register(actions, "capacity", help="Show the active review policy and available automatic work slots.")

    packet = register(actions, "packet", help="Read a frozen claim alongside its verification and open questions.")
    packet.add_argument("evidence_id", type=UUID)
    packet.add_argument("--run-directory", type=Path, help="Locate artifacts from an older or custom run.")
    explain = register(actions, "explain", help="Read a compact explanation from the existing evidence.")
    explain.add_argument("evidence_id", type=UUID)
    explain.add_argument("--run-directory", type=Path, help="Locate artifacts from an older or custom run.")

    disclosure = register(actions, "disclosure", outputs=(Output("output", "exports", external=True),), help="Export shareable provenance and recorded AI involvement.")
    disclosure.add_argument("evidence_id", type=UUID)
    disclosure.add_argument("--output", type=Path, help="Write a new JSON file at this path.")

    understanding = register(actions, "understanding", help="Save a researcher's explanation and takeaway.")
    understanding.add_argument("evidence_id", type=UUID)
    understanding.add_argument("--actor", required=True)
    understanding.add_argument("--explanation", required=True)
    understanding.add_argument("--lesson", required=True)

    local = register(actions, "import-source", help="Archive an original PDF supplied by the researcher.")
    fetch = register(actions, "fetch-source", help="Download and archive the specified original arXiv PDF.")
    for action in (local, fetch):
        action.add_argument("claim_id", type=UUID)
        action.add_argument("identifier")
        action.add_argument("--metadata", type=Path, required=True)
    local.add_argument("pdf", type=Path)

    export = register(actions, "export-source", outputs=(Output("file", "exports", external=True),), help="Write the checked archived PDF to a path for review.")
    export.add_argument("source_id", type=UUID)
    export.add_argument("file", type=Path)

    confirm = register(actions, "confirm-source", help="Confirm the exact archived source record.")
    confirm.add_argument("source_id", type=UUID)
    confirm.add_argument("--hash", required=True, dest="checksum")
    _identity(confirm)

    sources = register(actions, "sources", help="Find registered originals by claim or exact identifier.")
    sources.add_argument("claim_id", type=UUID, nargs="?")
    sources.add_argument("--identifier")

    novelty = register(actions, "record-novelty", help="Record an explicitly scoped researcher judgment.")
    novelty.add_argument("claim_id", type=UUID)
    novelty.add_argument("assessment", choices=("not_found", "known", "distinct"))
    novelty.add_argument("--source", type=UUID, action="append", default=[])
    novelty.add_argument("--search", type=UUID, action="append", default=[])
    novelty.add_argument("--report", type=Path, required=True)
    novelty.add_argument("--evidence", type=UUID)
    _identity(novelty)

    status = register(actions, "novelty", help="Show whether a literature review remains current.")
    status.add_argument("claim_id", type=UUID)


def _query(store, args):
    action = args.review_action
    if action == "inbox":
        if not 1 <= args.limit <= 1000:
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
        """, args.claim, args.claim, args.limit)
    if action == "policy":
        return store.query("human", "SELECT * FROM rm.record_research_review_policy(%s,%s,%s,%s,%s)",
                           args.max_pending, args.weekly_minutes, args.require_understanding, args.actor, args.reason)[0]
    if action == "capacity":
        settings = store.query("human", "SELECT * FROM rm.research_review_policies ORDER BY sequence_no DESC LIMIT 1")
        return {"policy": settings[0] if settings else None, "available_slots": store.query(
            "human", "SELECT rm.research_review_capacity() AS slots")[0]["slots"]}
    if action == "packet":
        return research_review.evidence_packet(store, args.evidence_id, args.run_directory)
    if action == "explain":
        return research_review.explain_evidence(store, args.evidence_id, args.run_directory)
    if action == "disclosure":
        report = review_disclosure.export(store, args.evidence_id)
        if args.output:
            with args.output.open("x", encoding="utf-8") as handle:
                handle.write(json_text(report) + "\n")
        return report
    if action == "understanding":
        return store.query("human", "SELECT rm.record_research_understanding(%s,%s,%s,%s) AS id",
                           args.evidence_id, args.actor, args.explanation, args.lesson)[0]
    if action in {"import-source", "fetch-source"}:
        metadata = json.loads(args.metadata.read_bytes())
        return research_review.archive_source(store, args.claim_id, args.identifier, metadata, getattr(args, "pdf", None))
    if action == "export-source":
        original = research_review.archived_source(store, args.source_id)
        with args.file.open("xb") as handle:
            handle.write(original)
        return {"source_id": args.source_id, "file": str(args.file)}
    if action == "confirm-source":
        return research_review.confirm_source(store, args.source_id, args.checksum, args.actor, args.reason)
    if action == "sources":
        identifier = research_review.normalize_identifier(args.identifier) if args.identifier else None
        if args.claim_id is None and identifier is None:
            raise ValueError("Provide a claim ID or a literature identifier.")
        return store.query("human", """
            SELECT * FROM rm.literature_source_status
            WHERE (%s::uuid IS NULL OR claim_id = %s)
              AND (%s::text IS NULL OR body ->> 'identifier' = %s)
            ORDER BY recorded_at, id
        """, args.claim_id, args.claim_id, identifier, identifier)
    if action == "record-novelty":
        report = json.loads(args.report.read_bytes())
        if not isinstance(report, dict):
            raise ValueError("A novelty report must be a JSON object.")
        if args.search and "search_ids" in report:
            raise ValueError("Specify search IDs with --search or search_ids in the report.")
        if args.search:
            report["search_ids"] = [str(identifier) for identifier in args.search]
        search_ids = report.get("search_ids", [])
        if not isinstance(search_ids, list) or len(search_ids) > 50 or any(not isinstance(item, str) for item in search_ids):
            raise ValueError("search_ids must be an array of up to 50 recorded search IDs.")
        for search in search_ids:
            literature.verify_search_archive(store, UUID(search))
        for source in args.source:
            research_review.archived_source(store, source)
        return store.query("human", "SELECT rm.record_novelty_review(%s,%s,%s,%s::uuid[],%s,%s,%s) AS id",
                           args.claim_id, args.evidence, args.assessment, args.source, store.json(report),
                           args.actor, args.reason)[0]
    status = store.query("human", "SELECT * FROM rm.novelty_review_status WHERE claim_id = %s", args.claim_id)
    if not status:
        raise ValueError("No frozen claim matches this novelty query.")
    return status[0]


def run(args):
    with Store() as store:
        result = _query(store, args)
    if args.review_action == "explain":
        print(result)
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=_encode))
    return 0
