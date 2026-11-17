"""Manage research basis versions, task state and local artifact storage."""

from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
import sys
from uuid import UUID

from research_multiplier.policy import basis, project_budget
from research_multiplier.storage import paths, project_archive, workspace_archive
from research_multiplier.workflows import research_followup, research_resume
from research_multiplier.storage.postgres import Store, bind_workspace


from ._command import register, Output
from ._output import json_default as _encode


def _add_model_budget(actions):
    budget = register(actions, "model-budget", help="Inspect or set the project's monthly model allowance.")
    modes = budget.add_subparsers(dest="budget_action", required=True)
    register(modes, "status", help="Show approved routes and current monthly reservations.")
    limit = register(modes, "limit", help="Sign a USD allowance for each UTC calendar month.")
    allowance = limit.add_mutually_exclusive_group(required=True)
    allowance.add_argument("usd", type=Decimal, nargs="?")
    allowance.add_argument("--unlimited", action="store_true")
    tariff = register(modes, "tariff", help="Sign the maximum charge for an exact configured model.")
    tariff.add_argument("role", choices=("PROVER", "AUDITOR", "EXPLORE", "LEAN_AUTHOR"))
    tariff.add_argument("maximum_usd", type=Decimal)
    route_choice = tariff.add_mutually_exclusive_group()
    route_choice.add_argument("--private", action="store_true", help="Use the separately configured local role.")
    route_choice.add_argument("--profile", help="Use a named PROVER profile configured for a shadow trial.")
    tariff.add_argument("--max-input-bytes", type=int, required=True)
    tariff.add_argument("--max-output-tokens", type=int, required=True)
    for command in (limit, tariff):
        command.add_argument("--actor", required=True)
        command.add_argument("--reason", required=True)


def add_commands(commands):
    research = commands.add_parser("research", help="Manage reusable research evidence and durable execution.")
    research.set_defaults(_handler=run)
    actions = research.add_subparsers(dest="research_action", required=True)

    register(actions, "paths", roles=(), help="Show where Research Multiplier keeps its runtime files.")
    init = register(actions, "init-workspace", roles=(), workspace=False, help="Organize an independent research project directory.")
    init.add_argument("directory", type=Path, nargs="?")
    register(actions, "bind-workspace", roles=(), help="Bind the selected project to its configured PostgreSQL database.")
    backup = register(actions, "backup-project", roles=(), outputs=(Output("out", "backups", external=True),), help="Archive the project's PostgreSQL snapshot and project files.")
    backup.add_argument("--out", type=Path)
    backup.add_argument("--pg-bin", type=Path)
    verify = register(actions, "verify-project-backup", roles=(), workspace=False, help="Check a complete project archive's hashes.")
    verify.add_argument("directory", type=Path)
    restore = register(actions, "restore-project", roles=(), workspace=False, outputs=(Output("destination", external=True),), help="Restore a trusted archive into a new folder and empty database.")
    restore.add_argument("archive", type=Path)
    restore.add_argument("destination", type=Path)
    restore.add_argument("--pg-bin", type=Path)
    followup = register(actions, "follow-up", help="Find prospective next scopes from a current adopted Lean proof.")
    followup.add_argument("identifier", type=UUID, help="Claim ID, or the prepared frame ID with --propose.")
    next_scope = followup.add_mutually_exclusive_group()
    next_scope.add_argument("--prepare", metavar="CANDIDATE_HASH")
    next_scope.add_argument("--propose", action="store_true")
    _add_model_budget(actions)
    register(actions, "components", help="List versioned research infrastructure components.")
    active = register(actions, "basis", help="List the currently usable versions for a confirmed packet.")
    active.add_argument("packet_id")
    status = register(actions, "tasks", help="Show tasks, waiting questions, time and usage.")
    status.add_argument("claim_id")
    resume = register(actions, "resume", help="Summarize current research, decisions and recorded progress.")
    resume.add_argument("claim_id", type=UUID, nargs="?")
    resume.add_argument("--limit", type=int, default=research_resume.DEFAULT_LIMIT)
    resume.add_argument("--details", action="store_true", help="Include exact versions, hashes and task references.")
    resume.add_argument("--format", choices=("json", "text"), default="json",
                        help="Use text for a decisions-first reading view.")
    clarify = register(actions, "clarify", help="Read or answer a formalization question.")
    clarify.add_argument("identifier", type=UUID)
    clarification_action = clarify.add_mutually_exclusive_group()
    clarification_action.add_argument("--answers", type=Path, help="Read a JSON array of answers in question order.")
    clarification_action.add_argument("--dismiss", action="store_true", help="Close a question with a recorded reason.")
    clarification_action.add_argument("--continue", dest="continue_formalization", action="store_true",
                                      help="Use the saved answers to draft the same target.")
    clarification_scope = clarify.add_mutually_exclusive_group()
    clarification_scope.add_argument("--confirm-public", action="store_true")
    clarification_scope.add_argument("--confirm-local", action="store_true")
    clarify.add_argument("--actor")
    clarify.add_argument("--reason")
    cancel = register(actions, "cancel", help="Cancel an unfinished task.")
    cancel.add_argument("job_id")
    cancel.add_argument("--actor", required=True)
    cancel.add_argument("--reason", required=True)

    propose = register(actions, "propose-item", help="Record a new version of a definition, hypothesis or theorem.")
    propose.add_argument("key")
    propose.add_argument("file", type=Path)
    approve = register(actions, "approve-item", help="Confirm the exact meaning of one item version.")
    approve.add_argument("version_id")
    approve.add_argument("--hash", required=True, dest="checksum")
    approve.add_argument("--actor", required=True)
    approve.add_argument("--reason", required=True)
    release = register(actions, "release-item", help="Authorize an exact admitted version for public model calls.")
    release.add_argument("version_id")
    release.add_argument("--hash", required=True, dest="checksum")
    release.add_argument("--actor", required=True)
    release.add_argument("--reason", required=True)
    attach = register(actions, "attach-item", help="Admit an approved library version into a packet's basis.")
    attach.add_argument("packet_id")
    attach.add_argument("version_id")
    attach.add_argument("--reason", required=True)

    retire = register(actions, "retire-item", help="Mark an item version and its dependent results for recheck.")
    retire.add_argument("version_id")
    retire.add_argument("--reason", required=True)
    retire_input = register(actions, "retire-input", help="Invalidate proofs affected by a trusted input revision.")
    retire_input.add_argument("kind", choices=("source", "checker", "environment"))
    retire_input.add_argument("checksum")
    retire_input.add_argument("--reason", required=True)
    evidence = register(actions, "evidence", help="Show verification, review and current use of evidence.")
    evidence.add_argument("claim_id")
    reopen = register(actions, "reopen", help="Reopen a claim whose decisive evidence requires recheck.")
    reopen.add_argument("claim_id")
    reopen.add_argument("--actor", required=True)
    reopen.add_argument("--reason", required=True)

    time = register(actions, "log-time", help="Record actual researcher time spent on a task.")
    time.add_argument("claim_id")
    time.add_argument("activity", choices=("review", "clarification"))
    time.add_argument("--minutes", required=True, type=Decimal)
    for name in ("backup-artifacts", "restore-artifacts"):
        paths_spec = (Output("directory", "backups", external=True),) if name == "backup-artifacts" else ()
        action = register(actions, name, roles=(), outputs=paths_spec,
                          help=f"{name.replace('-', ' ').capitalize()} using RM_ARTIFACT_ROOT.")
        action.add_argument("directory", type=Path)


def _query(args):
    with Store() as store:
        action = args.research_action
        if action == "resume":
            return research_resume.snapshot(store, args.claim_id, limit=args.limit, details=args.details)
        if action == "clarify":
            return _clarify(store, args)
        if action == "follow-up":
            if args.propose:
                return research_followup.propose(store, args.identifier)
            return research_followup.prepare(store, args.identifier, args.prepare)
        if action == "model-budget":
            return project_budget.command(args, store)
        if action == "components":
            return store.query("human", "SELECT * FROM rm.harness_components ORDER BY name, version")
        if action == "basis":
            return basis.versions(store, "human", args.packet_id)
        if action == "tasks":
            return {
                "tasks": store.query("human", """
                    SELECT * FROM rm.research_task_status WHERE claim_id = %s ORDER BY enqueued_at, id
                """, args.claim_id),
                "clarifications": store.query("human", """
                    SELECT c.* FROM rm.formalization_clarification_status c
                    JOIN rm.formalization_status p ON p.id = c.packet_id
                    WHERE p.claim_id = %s ORDER BY c.requested_at
                """, args.claim_id),
                "researcher_time": store.query("human", """
                    SELECT activity, sum(minutes) AS minutes FROM rm.human_time_entries
                    WHERE claim_id = %s GROUP BY activity
                """, args.claim_id),
            }
        if action == "cancel":
            return store.query("human", "SELECT rm.cancel_research_job(%s,%s,%s) AS id",
                               args.job_id, args.actor, args.reason)[0]
        if action == "propose-item":
            body = json.loads(args.file.read_bytes())
            return store.query("human", "SELECT * FROM rm.propose_formal_item(%s,%s)", args.key, store.json(body))[0]
        if action == "approve-item":
            return store.query("human", "SELECT rm.approve_formal_item(%s,%s,%s,%s) AS id",
                               args.version_id, args.checksum, args.actor, args.reason)[0]
        if action == "release-item":
            return store.query("human", "SELECT rm.release_formal_item(%s,%s,%s,%s) AS id",
                               args.version_id, args.checksum, args.actor, args.reason)[0]
        if action == "attach-item":
            return store.query("human", "SELECT rm.attach_formal_item(%s,%s,%s) AS id",
                               args.packet_id, args.version_id, args.reason)[0]
        if action == "retire-item":
            store.query("human", "SELECT rm.retire_formal_item(%s,%s)", args.version_id, args.reason)
            return {"retired": args.version_id}
        if action == "retire-input":
            store.query("human", "SELECT rm.retire_verification_input(%s,%s,%s)",
                        "verifier" if args.kind == "checker" else args.kind, args.checksum, args.reason)
            return {"retired": args.checksum, "kind": args.kind}
        if action == "reopen":
            return store.query("human", "SELECT rm.reopen_stale_claim(%s,%s,%s) AS id",
                               args.claim_id, args.actor, args.reason)[0]
        if action == "log-time":
            if not args.minutes.is_finite() or args.minutes <= 0:
                raise ValueError("Enter a positive number of minutes.")
            return store.query("human", "SELECT rm.log_human_time(%s,%s,%s) AS id",
                               args.claim_id, args.activity, args.minutes)[0]
        return {
            "claim": store.query("human", "SELECT * FROM rm.claim_foundation_status WHERE id = %s", args.claim_id),
            "evidence": store.query("human", """
                SELECT * FROM rm.evidence_foundation_status WHERE claim_id = %s ORDER BY issued_at, id
            """, args.claim_id),
        }


def _clarify(store, args):
    from research_multiplier.workflows import target_clarifications

    if args.continue_formalization:
        if args.actor or args.reason or not (args.confirm_public or args.confirm_local):
            raise ValueError("Use --continue with --confirm-public or --confirm-local and the already recorded answers.")
        result = target_clarifications.continue_formalization(store, args.identifier, confirm_local=args.confirm_local)
        if result.get("target"):
            result["review_command"] = "research-multiplier intake preview-target --from-proposal " + result["record"]["sha256"]
        return result
    if args.confirm_public or args.confirm_local:
        raise ValueError("Use model disclosure flags only with --continue.")
    if args.answers or args.dismiss:
        if not args.actor or not args.reason:
            raise ValueError("Recording an answer or dismissal requires --actor and --reason.")
        answers = None
        if args.answers:
            with args.answers.open("rb") as handle:
                raw = handle.read(64001)
            if len(raw) > 64000:
                raise ValueError("Clarification answers are limited to 64 KiB.")
            answers = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(answers, list):
                raise ValueError("Supply the answers as a JSON array in question order.")
        return target_clarifications.answer(store, args.identifier, answers, args.actor, args.reason)
    if args.actor or args.reason:
        raise ValueError("Use --actor and --reason when recording answers or a dismissal.")
    return target_clarifications.show(store, args.identifier)


def run(args):
    if args.research_action == "paths":
        result = {
            "data_root": str(paths.data_root()),
            "inputs": str(paths.data_root() / "inputs"),
            "runs": str(paths.runs()),
            "offline_runs": str(paths.offline_runs()),
            "claim_runs": str(paths.claim_runs()),
            "objects": str(paths.object_store()),
            "exports": str(paths.data_root() / "exports"),
            "backups": str(paths.data_root() / "backups"),
            "evaluation": str(paths.data_root() / "evaluation" / "private"),
            "lean_runs": str(paths.runs() / "lean"),
            "lean_project": str(paths.lean_project()),
            "database_id": paths.database_id(paths.active_workspace()),
        }
    elif args.research_action == "init-workspace":
        directory = args.directory
        if directory is None:
            if not sys.stdin.isatty():
                raise ValueError("Supply the folder path to 'research init-workspace'.")
            try:
                print("Workspace folders will include " + ", ".join(paths.WORKSPACE_DIRECTORIES) + ".", file=sys.stderr)
                value = input("Choose the folder for this research workspace. Path: ").strip()
            except (EOFError, KeyboardInterrupt):
                raise ValueError("A research workspace folder is required.") from None
            if not value:
                raise ValueError("A research workspace folder is required.")
            directory = Path(value)
        root = paths.initialize_workspace(directory)
        from research_multiplier.workflows import agent_guide

        result = {"workspace": str(root), "directories": list(paths.WORKSPACE_DIRECTORIES),
                  "agent_files": agent_guide.install(root)}
    elif args.research_action == "bind-workspace":
        root = paths.active_workspace()
        if root is None:
            raise ValueError("Select an initialized research workspace before binding its database.")
        result = {"workspace": str(root), "database_id": bind_workspace(root)}
    elif args.research_action == "backup-project":
        result = project_archive.backup(args.out, pg_bin=args.pg_bin)
    elif args.research_action == "verify-project-backup":
        result = project_archive.verify(args.directory)
    elif args.research_action == "restore-project":
        result = project_archive.restore(args.archive, args.destination, pg_bin=args.pg_bin)
    elif args.research_action == "backup-artifacts":
        result = workspace_archive.backup(args.directory)
    elif args.research_action == "restore-artifacts":
        result = workspace_archive.restore(args.directory)
    else:
        result = _query(args)
    if args.research_action == "resume" and args.format == "text":
        print(research_resume.render(result))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=_encode))
    return 0
