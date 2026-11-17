"""Command-line entry points for Research Multiplier."""

import argparse
from decimal import Decimal, InvalidOperation
import json
import os
import sys
import tempfile
from pathlib import Path

from research_multiplier import models
from research_multiplier.storage import paths
from research_multiplier.workflows import workflow
from research_multiplier.workflows.runtime import VerificationQueueError

from ._command import ALL, PROVE, VERIFIER, Output, preflight, prepare_paths, register


PROBLEM_KINDS = ("open", "graph-coloring", "linear-program")
CASE_NAMES = ("graph-coloring", "linear-program", "open-question")


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{path}: invalid JSON at line {error.lineno}, column {error.colno}") from error


def _external_case(name, cases_dir):
    if cases_dir is None:
        configured = os.environ.get("RM_CASES_DIR")
        if not configured:
            raise ValueError("Choose an external case folder with --cases-dir or RM_CASES_DIR.")
        cases_dir = Path(configured)
    case = cases_dir.expanduser() / "offline" / f"{name}.json"
    return load_json(paths.validate_project_read(case))


def _problem_kind(value):
    return "open" if value == "open-question" else value


def _problem_template(kind):
    problem = {"kind": kind, "title": "", "question": "", "success": "",
               "assumptions": [], "notes": [], "references": []}
    if kind == "graph-coloring":
        problem["data"] = {"vertices": [], "edges": [], "colors": None}
    elif kind == "linear-program":
        problem["data"] = {field: [] for field in ("A", "b", "c", "primal", "dual")}
    return problem


def write_run(run, output):
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="run-", dir=root))
    (directory / "report.md").write_text(workflow.report(run), encoding="utf-8")
    (directory / "run.json").write_text(workflow.json_text(run) + "\n", encoding="utf-8")
    return directory


def build_parser():
    parser = argparse.ArgumentParser(
        prog="research-multiplier", description="Turn a research question into a checkable next step."
    )
    parser.add_argument("--workspace", type=Path, help="Use an initialized research project directory.")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = register(commands, "demo", handler=_simple, roles=(), outputs=(Output("out", "runs/offline"),), help="Run an explicitly supplied external case offline.")
    demo.add_argument("example", choices=CASE_NAMES, default="graph-coloring", nargs="?")
    demo.add_argument("--cases-dir", type=Path, help="Case folder containing offline/ (or set RM_CASES_DIR).")
    demo.add_argument("--out", type=Path, help="Folder under the workspace's runs/offline/ directory.")

    init = register(commands, "init", handler=_simple, roles=(), outputs=(Output("path", "inputs"),), help="Write an editable problem template.")
    init.add_argument("path", type=Path)
    init.add_argument("--kind", "--example", type=_problem_kind, choices=PROBLEM_KINDS, default="open")

    run = register(commands, "run", handler=_simple, roles=(), outputs=(Output("out", "runs/offline"),), help="Run a check on a problem file and save a report.")
    run.add_argument("problem", type=Path)
    run.add_argument("--out", type=Path, help="Folder under the workspace's runs/offline/ directory.")
    run.add_argument("--model", help="Ask this model for suggestions and send the problem to --base-url.")
    run.add_argument("--base-url", help="Chat Completions API base URL, including its version path.")

    replay = register(commands, "replay", handler=_simple, roles=(), workspace=False, help="Rerun the recorded check against its saved input.")
    replay.add_argument("record", type=Path, help="Path to a run.json file.")
    production = register(commands, "v0a", handler=_simple, roles=ALL, outputs=(Output("out", "runs/claims"),), help="Run the PostgreSQL-backed public flow-and-cut workflow.")
    production.add_argument("network", type=Path, help="Fixed directed network in JSON.")
    production.add_argument("--title", required=True)
    production.add_argument("--record-freeze-as", required=True, metavar="OPERATOR")
    production.add_argument("--out", type=Path, help="Folder under the workspace's runs/claims/ directory.")
    resume = register(commands, "resume", handler=_simple, roles=PROVE, outputs=(Output("directory", "runs/claims"),), help="Continue a PostgreSQL v0a run from its saved stages.")
    resume.add_argument("directory", type=Path)
    resume.add_argument("--retry", action="store_true", help="Retry a stage that recorded a retryable failure.")
    continue_audit = register(commands, "resume-audit", handler=_simple, roles=PROVE, outputs=(Output("directory", "runs/claims"),), help="Retry an unfinished independent v0a audit.")
    continue_audit.add_argument("directory", type=Path)
    review = register(commands, "review-evidence", handler=_simple, help="Record a human approval or return.")
    review.add_argument("evidence_id")
    review.add_argument("action", choices=("approve", "return"))
    review.add_argument("--actor", required=True)
    review.add_argument("--reason", required=True)
    review.add_argument("--explanation", help="Explain the proof's key step in your own words.")
    review.add_argument("--lesson", help="Record what the result taught you.")
    review.add_argument("--minutes", help="Record actual review time with this decision.")
    promote = register(commands, "promote-evidence", handler=_simple, roles=VERIFIER, help="Apply an approved verifier receipt.")
    promote.add_argument("evidence_id")
    promote.add_argument("--reason", required=True)
    from . import (
        advance_cli, doctor_cli, eval_cli, formalization_cli, goal_cli, intake_cli, lean_cli, literature_cli,
        plan_cli, research_cli, review_cli, scope_cli, trust_cli,
    )

    advance_cli.add_commands(commands)
    doctor_cli.add_commands(commands)
    eval_cli.add_commands(commands)
    formalization_cli.add_commands(commands)
    goal_cli.add_commands(commands)
    intake_cli.add_commands(commands)
    lean_cli.add_commands(commands)
    literature_cli.add_commands(commands)
    plan_cli.add_commands(commands)
    research_cli.add_commands(commands)
    review_cli.add_commands(commands)
    scope_cli.add_commands(commands)
    trust_cli.add_commands(commands)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        with paths.use_workspace(args.workspace, strict=args._command.workspace or args.workspace is not None):
            prepare_paths(args)
            preflight(args)
            return args._handler(args)
    except FileExistsError as error:
        print(f"Already exists: {error.filename}", file=sys.stderr)
    except (ValueError, OSError, VerificationQueueError) as error:
        print(f"Error: {error}", file=sys.stderr)
    return 2


def _simple(args):
    if args.command == "resume":
        from ..production import resume

        result = resume(args.directory, retry=args.retry)
        print(f"Evidence: {result['evidence_id']}")
        print(f"Claim: {result['claim_status']}. Human review: {result['human_review']}")
        return 0
    if args.command == "resume-audit":
        from ..production import resume_audit

        print(f"Evidence awaiting human review: {resume_audit(args.directory)}")
        return 0
    if args.command == "review-evidence":
        from ..production import review_evidence

        minutes = None
        if args.minutes is not None:
            try:
                minutes = Decimal(args.minutes)
            except InvalidOperation:
                raise ValueError("Enter a positive number of review minutes.") from None
            if not minutes.is_finite() or not 0 < minutes <= 10080:
                raise ValueError("Enter a positive review duration of at most one week.")
        decision = review_evidence(args.evidence_id, args.action, args.actor, args.reason,
                                   explanation=args.explanation, lesson=args.lesson, minutes=minutes)
        print(f"Decision: {decision}")
        return 0
    if args.command == "promote-evidence":
        from ..production import promote_evidence

        print(f"Claim: {promote_evidence(args.evidence_id, args.reason)}")
        return 0
    if args.command == "v0a":
        from ..production import run_public

        directory, result = run_public(load_json(args.network), args.title, args.out or paths.claim_runs(),
                                       args.record_freeze_as)
        print(f"Claim: {result['subject']['claim_id']}")
        print(f"Evidence: {result['evidence_id']}")
        print(f"Human review pending: {directory}")
        return 0
    if args.command == "init":
        with args.path.open("x", encoding="utf-8") as stream:
            stream.write(workflow.json_text(_problem_template(args.kind)) + "\n")
        print(f"Edit {args.path} and run it with the 'run' command.")
        return 0
    if args.command == "replay":
        record = load_json(args.record)
        if isinstance(record, dict) and ("workflow" in record or {"subject", "contract"} <= record.keys()):
            from ..production import replay

            reproduced = replay(args.record)
        else:
            reproduced = workflow.replay(record)
        if not reproduced:
            print("The recorded result differs from the current replay.", file=sys.stderr)
            return 1
        print("Recorded check reproduced.")
        return 0
    if args.command == "demo":
        run = workflow.start(_external_case(args.example, args.cases_dir))
    else:
        if bool(args.model) != bool(args.base_url):
            raise ValueError("--model and --base-url must be given together")
        run = workflow.start(load_json(args.problem))
    directory = write_run(run, args.out or paths.offline_runs())
    print(f"Check status: {run['result']['status']}\nReport: {directory / 'report.md'}")
    print(f"Saved record: {directory / 'run.json'}")
    if args.command == "run" and args.model:
        draft = models.suggest(run["problem"], run["result"], args.model, args.base_url)
        (directory / "model-suggestions.md").write_text(
            "# Model suggestions for review\n\n"
            f"Model: {draft['model']}\n\n"
            f"{draft['text']}\n",
            encoding="utf-8",
        )
        print(f"Model suggestions: {directory / 'model-suggestions.md'}")
    return 0
