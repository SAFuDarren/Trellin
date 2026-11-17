"""Report checker issues, inspect observations and record containment decisions."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import UUID

from research_multiplier.storage import paths
from research_multiplier.storage.artifacts import Artifacts
from research_multiplier.storage.postgres import Store


from ._command import register, HUMAN, VERIFIER, VERIFY
from ._output import json_default as _encode


def _instant(value):
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError("Specify a time zone for trust scan boundaries.")
    return parsed


def add_commands(commands):
    trust = commands.add_parser("trust", help="Inspect verification and record incident containment.")
    trust.set_defaults(_handler=run)
    actions = trust.add_subparsers(dest="trust_action", required=True)
    report = register(actions, "report", roles=lambda args: (args.role,), help="Archive a reproducible verifier vulnerability or workflow block.")
    report.add_argument("file", type=Path)
    report.add_argument("--kind", choices=("verifier_vulnerability", "workflow_blocked"), required=True)
    report.add_argument("--summary", required=True)
    report.add_argument("--job", type=UUID)
    report.add_argument("--checker", help="A recorded checker revision. A scoped job defaults to its local checker.")
    report.add_argument("--role", choices=("human", "verifier", "scheduler", "agent"), default="human")
    status = register(actions, "status", help="Read incident summaries, responsible decision and checker status.")
    status.add_argument("report_id", type=UUID, nargs="?")
    decide = register(actions, "decide", help="Contain or dismiss an incident with a signed reason.")
    decide.add_argument("report_id", type=UUID)
    decide.add_argument("action", choices=("contain", "dismiss"))
    decide.add_argument("--actor", required=True)
    decide.add_argument("--reason", required=True)
    scan = register(actions, "scan", help="Compare verifier counts for two adjacent time windows.")
    scan.add_argument("--from", dest="start", type=_instant, required=True)
    scan.add_argument("--to", dest="end", type=_instant, required=True)
    policy = register(actions, "policy", help="Review thresholds for automatic checker holds.")
    policy.add_argument("--min-attempts", type=int, required=True)
    policy.add_argument("--jump-percent", type=int, required=True)
    policy.add_argument("--actor", required=True)
    policy.add_argument("--reason", required=True)
    monitor = register(actions, "monitor", roles=VERIFIER, help="Apply the reviewed anomaly policy to a verification window.")
    monitor.add_argument("--from", dest="start", type=_instant)
    monitor.add_argument("--to", dest="end", type=_instant)
    monitor.add_argument("--last-hours", type=int, help="Compare the most recent hours with the preceding window.")
    sample = register(actions, "sample", help="Select current adopted evidence for an independent deep review.")
    sample.add_argument("--from", dest="start", type=_instant, required=True)
    sample.add_argument("--to", dest="end", type=_instant, required=True)
    sample.add_argument("--seed", required=True, help="Record a seed to reproduce the selection on the same evidence.")
    sample.add_argument("--limit", type=int, default=5)
    regression = register(actions, "regression", help="Link a regression test and its exact fixture to an incident.")
    regression.add_argument("report_id", type=UUID)
    regression.add_argument("file", type=Path)
    regression.add_argument("--test-case", required=True)
    regression.add_argument("--actor", required=True)
    regression.add_argument("--reason", required=True)
    regressions = register(actions, "regressions", help="List recorded regression tests for an incident.")
    regressions.add_argument("report_id", type=UUID)
    lean_regression = register(actions, "lean-regression", roles=(*HUMAN, *VERIFIER), help="Capture an incident as an executable Lean regression.")
    lean_regression.add_argument("report_id", type=UUID)
    lean_regression.add_argument("--expect", required=True, choices=("rejected", "verified"),
                                 help="The reviewed outcome required on replay.")
    lean_regression.add_argument("--actor", required=True)
    lean_regression.add_argument("--reason", required=True)
    replay_lean = register(actions, "replay-lean-regression", roles=(*HUMAN, *VERIFY), help="Run one recorded case in a restored workspace.")
    replay_lean.add_argument("regression_id", type=UUID)
    replay_lean.add_argument("--lean")
    replay_lean.add_argument("--mathlib-project", type=Path)
    recheck_scan = register(actions, "lean-recheck-scan", roles=VERIFIER, help="Record Lean evaluation signals and queue development cases.")
    recheck_scan.add_argument("--limit", type=int, default=32, help="Maximum new executable fixtures (1 to 128).")
    recheck_scan.add_argument("--campaign", help="Digest of a sealed development campaign to observe.")
    recheck_status = register(actions, "lean-recheck-status", help="Read Lean evaluation signals and replay outcomes.")
    recheck_status.add_argument("--limit", type=int, default=32)
    recheck_work = register(actions, "lean-recheck-work", roles=(*HUMAN, *VERIFY), help="Run queued development cases in a restored workspace.")
    recheck_work.add_argument("--limit", type=int, default=1)
    recheck_work.add_argument("--retry-inconclusive", action="store_true")
    recheck_work.add_argument("--lean")
    recheck_work.add_argument("--mathlib-project", type=Path)
    register(actions, "holds", help="Read automatic checker holds and researcher release decisions.")
    release = register(actions, "release", help="Release an automatic checker hold after review.")
    release.add_argument("hold_id", type=UUID)
    release.add_argument("--actor", required=True)
    release.add_argument("--reason", required=True)


def _execute(store, args):
    if args.trust_action == "report":
        data = args.file.read_bytes()
        if not data:
            raise ValueError("The incident report file is empty.")
        artifact = Artifacts(paths.object_store()).put_bytes(data)
        return store.query(args.role, "SELECT * FROM rm.report_verifier_incident(%s,%s,%s,%s,%s)",
                           args.kind, args.summary, store.json(artifact), args.job, args.checker)[0]
    if args.trust_action == "status":
        if args.report_id:
            rows = store.query("human", "SELECT * FROM rm.verifier_incident_status WHERE id = %s", args.report_id)
            if not rows:
                raise ValueError("No verifier incident exists with this ID.")
            return rows[0]
        return store.query("human", "SELECT * FROM rm.verifier_incident_status ORDER BY created_at DESC, id")
    if args.trust_action == "decide":
        return store.query("human", "SELECT * FROM rm.decide_verifier_incident(%s,%s,%s,%s)",
                           args.report_id, args.action, args.actor, args.reason)[0]
    if args.trust_action == "policy":
        return store.query("human", "SELECT * FROM rm.record_checker_monitor_policy(%s,%s,%s,%s)",
                           args.min_attempts, args.jump_percent, args.actor, args.reason)[0]
    if args.trust_action == "monitor":
        if args.last_hours is not None:
            if args.start is not None or args.end is not None or not 1 <= args.last_hours <= 720:
                raise ValueError("Choose one relative window from 1 to 720 hours or supply exact dates.")
            end = datetime.now(timezone.utc)
            start = end - timedelta(hours=args.last_hours)
        else:
            if args.start is None or args.end is None:
                raise ValueError("Supply both --from and --to or a relative window.")
            start, end = args.start, args.end
        return store.query("verifier", "SELECT * FROM rm.monitor_checker_window(%s,%s)", start, end)
    if args.trust_action == "sample":
        if not 1 <= args.limit <= 100 or not 1 <= len(args.seed.strip()) <= 128 or args.start >= args.end:
            raise ValueError("Choose an ordered date range, a seed of 1 to 128 characters and 1 to 100 cases.")
        cases = store.query("human", """
            SELECT e.id AS evidence_id, e.claim_id, e.kind, e.issued_at
            FROM rm.evidence_foundation_status e JOIN rm.claim_foundation_status c ON c.id = e.claim_id
            WHERE e.id = c.decisive_evidence_id AND c.status IN ('proved', 'refuted')
              AND NOT e.needs_recheck AND e.review_status = 'approved'
              AND e.issued_at >= %s AND e.issued_at < %s
            ORDER BY md5(e.id::text || ':' || %s), e.id LIMIT %s
        """, args.start, args.end, args.seed, args.limit)
        return {"from": args.start, "to": args.end, "seed": args.seed, "cases": cases}
    if args.trust_action == "regression":
        with args.file.open("rb") as source:
            data = source.read(1024 * 1024 + 1)
        if not 1 <= len(data) <= 1024 * 1024:
            raise ValueError("Supply a nonempty regression fixture of at most 1 MiB.")
        artifact = Artifacts(paths.object_store()).put_bytes(data)
        return store.query("human", "SELECT * FROM rm.record_incident_regression(%s,%s,%s,%s,%s)",
                           args.report_id, args.test_case, store.json(artifact), args.actor, args.reason)[0]
    if args.trust_action == "regressions":
        return store.query("human", """
            SELECT * FROM rm.incident_regressions WHERE report_id = %s ORDER BY recorded_at, id
        """, args.report_id)
    if args.trust_action == "lean-regression":
        from research_multiplier.evaluations import lean_incident_regression

        return lean_incident_regression.capture(store, args.report_id, args.expect, args.actor, args.reason)
    if args.trust_action in {"lean-recheck-scan", "lean-recheck-status"}:
        from research_multiplier.evaluations import lean_rechecks

        if args.trust_action == "lean-recheck-scan":
            return lean_rechecks.scan(store, args.limit, campaign=args.campaign)
        return lean_rechecks.status(store, args.limit)
    if args.trust_action == "holds":
        return store.query("human", "SELECT * FROM rm.checker_hold_status ORDER BY recorded_at DESC, id")
    if args.trust_action == "release":
        return store.query("human", "SELECT * FROM rm.release_checker_hold(%s,%s,%s)",
                           args.hold_id, args.actor, args.reason)[0]
    return {"from": args.start, "to": args.end, "prior_window_start": args.start - (args.end - args.start),
            "observations": store.query("human", "SELECT * FROM rm.scan_verifier_window(%s,%s)", args.start, args.end)}


def run(args):
    if args.trust_action == "replay-lean-regression":
        from research_multiplier.evaluations import lean_incident_regression

        result = lean_incident_regression.replay(args.regression_id, lean=args.lean,
                                                mathlib_project=args.mathlib_project)
    elif args.trust_action == "lean-recheck-work":
        from research_multiplier.evaluations import lean_rechecks

        result = lean_rechecks.work(args.limit, retry=args.retry_inconclusive, lean=args.lean,
                                    mathlib_project=args.mathlib_project)
    else:
        with Store() as store:
            result = _execute(store, args)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=_encode))
    if args.trust_action == "replay-lean-regression" and result["status"] != "passed":
        return 1
    if args.trust_action == "lean-recheck-work" and any(row["status"] != "passed" for row in result["runs"]):
        return 1
    return 0
