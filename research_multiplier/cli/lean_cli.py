"""Command-line interface for reviewed Lean statements and proofs."""

import json
from datetime import datetime
from pathlib import Path
from uuid import UUID

from research_multiplier.workflows import lean_jobs, lean_workflow
from research_multiplier.proof import packaged_lean
from research_multiplier.storage import paths
from research_multiplier.storage.artifacts import write_bytes
from research_multiplier.proof.lean_runner import CANDIDATES, LeanProject


from ._command import register, ALL, HUMAN, PROVE, VERIFIER, VERIFY, Output
from ._output import json_default as _encode


def _search_roles(args):
    if not args.rounds:
        return VERIFY
    return ALL if args.confirm_local else PROVE


def add_commands(commands):
    lean = commands.add_parser("lean", help="Review and check Lean statements and proofs.")
    lean.set_defaults(_handler=run)
    actions = lean.add_subparsers(dest="lean_action", required=True)
    prepare = register(actions, "prepare", roles=(), outputs=(Output("project_dir", "lean-project"),), help="Copy the pinned Lean and Mathlib setup into a runtime project.")
    prepare.add_argument("source", type=Path, nargs="?",
                         help="Verified setup directory or external legacy sources (default: pinned Lean setup).")
    prepare.add_argument("--project-dir", type=Path, help="Destination under the workspace's lean-project/ directory.")
    inspect = register(actions, "inspect", roles=(), help="Inspect a statement or proof from a prepared legacy project.")
    inspect.add_argument("--candidate", choices=("describe", *CANDIDATES), default="describe")
    draft = register(actions, "draft", roles=(*HUMAN, *VERIFIER), help="Prepare the Lean statement from a confirmed legacy research packet.")
    draft.add_argument("packet_id")
    draft_theory = register(actions, "draft-theory", roles=(*HUMAN, *VERIFIER), help="Prepare the exact proposition for an approved Lean Core goal.")
    draft_theory.add_argument("packet_id")
    draft_theory.add_argument("--lean", help="Path to the pinned Lean compiler.")
    draft_theory.add_argument("--import", dest="modules", action="append", default=[], metavar="MODULE",
                              help="Import a Mathlib or project module in the stated order. Repeat as needed.")
    check_theory = register(actions, "check-theory", roles=lambda args: PROVE if args.model else VERIFY, outputs=(Output("out", "runs/lean"),), help="Independently verify a structured proof for a Lean Core goal.")
    check_theory.add_argument("statement_id")
    check_theory.add_argument("file", type=Path, nargs="?")
    check_theory.add_argument("--model", action="store_true", help="Ask the configured Lean author for a proof term.")
    check_theory.add_argument("--refute", action="store_true",
                              help="Verify the exact negation under the original approved assumptions.")
    check_theory.add_argument("--use-theorem", action="append", default=[], metavar="VERSION_ID",
                              help="Include an exact theorem version in this proof's approved context.")
    check_theory.add_argument("--lean", help="Path to the pinned Lean compiler.")
    check_theory.add_argument("--out", type=Path)
    check_theory.add_argument("--retry", action="store_true")
    check_source = register(actions, "check-source", roles=VERIFY, outputs=(Output("out", "runs/lean"),), help="Audit a local Lean file or a reviewed Lake declaration.")
    check_source.add_argument("statement_id", type=UUID)
    check_source.add_argument("file", nargs="?", type=Path, help="Ordinary local .lean theorem file.")
    check_source.add_argument("--module", help="Reviewed compiled module that defines the selected theorem.")
    check_source.add_argument("--declaration", required=True, help="Exact Lean declaration name.")
    check_source.add_argument("--lean", help="Path to the pinned Lean compiler.")
    check_source.add_argument("--out", type=Path)
    check_source.add_argument("--retry", action="store_true")
    reuse = register(actions, "reuse-theorem", roles=VERIFY, outputs=(Output("out", "runs/lean"),), help="Replay a native theorem using the destination's confirmed basis.")
    reuse.add_argument("statement_id", type=UUID)
    reuse.add_argument("version_id", type=UUID)
    reuse.add_argument("--lean", help="Path to the pinned Lean compiler.")
    reuse.add_argument("--out", type=Path)
    reuse.add_argument("--retry", action="store_true")
    formalpedia = register(actions, "replay-formalpedia", roles=VERIFY, outputs=(Output("out", "runs/lean"),),
                          help="Re-derive selected Formalpedia statements from the confirmed local basis.")
    formalpedia.add_argument("statement_id", type=UUID)
    formalpedia.add_argument("file", type=Path, help="Ordered local replay plan.")
    formalpedia.add_argument("--snapshot", type=Path, required=True)
    formalpedia.add_argument("--environment", required=True)
    formalpedia.add_argument("--lean", help="Path to the pinned Lean compiler.")
    formalpedia.add_argument("--out", type=Path)
    formalpedia.add_argument("--finish", action="store_true", help="Try to close the original frozen goal.")
    formalpedia.add_argument("--retry", action="store_true")
    form_sources = register(actions, "formalpedia-sources", roles=HUMAN,
                            help="Show source records for adopted Formalpedia working lemmas.")
    form_sources.add_argument("statement_id", type=UUID)
    form_sources.add_argument("--include-goals", action="store_true",
                              help="Include provenance for exact whole-goal receipts.")
    derive = register(actions, "derive", roles=VERIFY, outputs=(Output("out", "runs/lean"),), help="Verify scoped lemmas in order and check the original goal when supplied.")
    derive.add_argument("statement_id")
    derive.add_argument("file", type=Path)
    derive.add_argument("--lean", help="Path to the pinned Lean compiler.")
    derive.add_argument("--retry", action="store_true", help="Retry a failed verification job.")
    derive.add_argument("--out", type=Path, help="Folder for the original goal check. Defaults to the workspace.")
    search = register(actions, "search", roles=_search_roles, outputs=(Output("out", "runs/lean"),), help="Search a confirmed Lean scope with bounded model proposals.")
    search.add_argument("statement_id", type=UUID)
    disclosure = search.add_mutually_exclusive_group()
    disclosure.add_argument("--confirm-public", action="store_true",
                            help="Allow a model to see the reviewed public scope when rounds are enabled.")
    disclosure.add_argument("--confirm-local", action="store_true",
                            help="Use the trusted local model on a restricted reviewed scope.")
    search.add_argument("--rounds", type=int, default=3, help="Model proposal budget from 0 to 8 rounds.")
    search.add_argument("--lean", help="Path to the pinned Lean compiler.")
    search.add_argument("--retry", action="store_true", help="Retry failed jobs with the same inputs.")
    search.add_argument("--out", type=Path)
    budget = register(actions, "budget", help="Show or set the shared public Lean search call limit for a frozen goal.")
    budget.add_argument("statement_id", type=UUID)
    budget.add_argument("--history", action="store_true", help="Include every lease generation and limit change.")
    budget.add_argument("--limit", type=int, help="Set the total allowance, including already reserved calls.")
    budget.add_argument("--actor")
    budget.add_argument("--reason")
    reductions = register(actions, "reductions", roles=lambda args: VERIFY if args.register or args.work else VERIFIER, outputs=(Output("out", "runs/lean"),), help="Verify sufficient conditions and close a ready proof route.")
    reductions.add_argument("statement_id", type=UUID)
    reductions.add_argument("--register", type=Path, metavar="FILE", help="Check and register a version 1 reduction.")
    reductions.add_argument("--work", action="store_true", help="Check the first ready route against the original goal.")
    reductions.add_argument("--lean", help="Path to the pinned Lean compiler.")
    reductions.add_argument("--retry", action="store_true")
    reductions.add_argument("--out", type=Path)
    local_lemmas = register(actions, "local-theorems", roles=VERIFIER, help="List verified and stale lemmas in a reviewed scope.")
    local_lemmas.add_argument("statement_id")
    theorems = register(actions, "theorems", help="List current promoted Lean Core theorem versions.")
    theorems.add_argument("--limit", type=int, default=50)
    export = register(actions, "export-theorem", roles=VERIFIER, help="Recover a promoted Lean Core theorem from an archived run.")
    export.add_argument("evidence_id")
    export.add_argument("--lean", help="Path to the pinned Lean compiler for a historical native proof.")
    export.add_argument("--run-directory", type=Path, help="Directory containing the original core report and artifacts.")
    blueprint = register(actions, "blueprint", roles=lambda args: VERIFY if args.work else VERIFIER, outputs=(Output("out", "runs/lean"),), help="Inspect or check the ready nodes of a Lean Core proof graph.")
    blueprint.add_argument("file", type=Path)
    blueprint.add_argument("--work", action="store_true", help="Check ready reviewed nodes.")
    blueprint.add_argument("--limit", type=int, default=1, help="Maximum ready nodes to check (default: one).")
    blueprint.add_argument("--retry", action="store_true", help="Retry failed verification jobs.")
    blueprint.add_argument("--lean", help="Path to the pinned Lean compiler.")
    blueprint.add_argument("--out", type=Path)
    for action in (draft_theory, check_theory, check_source, reuse, export, blueprint, derive, search, reductions, formalpedia):
        action.add_argument("--mathlib-project", type=Path, metavar="PATH",
                            help="Local Lean project with its dependencies already compiled.")
    show = register(actions, "show", help="Read a Lean statement and its recorded checks.")
    show.add_argument("statement_id")
    confirm = register(actions, "confirm", help="Confirm the exact formal statement and its scope.")
    confirm.add_argument("statement_id")
    confirm.add_argument("--version", type=int, required=True)
    confirm.add_argument("--hash", dest="checksum", required=True)
    verify = register(actions, "verify", roles=VERIFIER, outputs=(Output("out", "runs/lean"),), help="Check a legacy project proof against its confirmed Lean statement.")
    verify.add_argument("statement_id")
    verify.add_argument("--candidate", choices=CANDIDATES, default="real")
    verify.add_argument("--out", type=Path, help="Folder for Lean run artifacts (default: data directory).")
    submit = register(actions, "submit", roles=lambda args: PROVE if args.model else VERIFY, outputs=(Output("out", "runs/lean"),), help="Queue a proof or intermediate theorem for a legacy project.")
    submit.add_argument("statement_id")
    submit.add_argument("file", type=Path, nargs="?")
    submit.add_argument("--target", choices=lean_jobs.TARGETS, default="goal")
    submit.add_argument("--model", action="store_true", help="Generate a proposal using the configured Lean author.")
    submit.add_argument("--retry", action="store_true", help="Retry a failed Lean author task when using --model.")
    submit.add_argument("--out", type=Path, help="Folder for Lean run artifacts (default: data directory).")
    work = register(actions, "work", roles=VERIFY, outputs=(Output("out", "runs/lean"),), help="Claim or recover a legacy Lean verification by job ID.")
    work.add_argument("job_id")
    work.add_argument("--retry", action="store_true")
    work.add_argument("--out", type=Path, help="Folder for Lean run artifacts (default: data directory).")
    review = register(actions, "review", help="Approve or return a checked Lean proof.")
    review.add_argument("check_id")
    review.add_argument("action", choices=("approve", "return"))
    for action in (inspect, draft, verify, submit, work):
        action.add_argument("--project-dir", type=Path,
                            help="Prepared legacy Lean project (default: existing project in the data directory).")
        action.add_argument("--lake", help="Path to Lake for the pinned toolchain.")
    for action in (confirm, review):
        action.add_argument("--actor", required=True)
        action.add_argument("--reason", required=True)


def _prepare(source, project):
    project = project.resolve()
    directory, sources = packaged_lean.read(source)
    if directory == project:
        raise ValueError("Use a different directory for the prepared Lean project.")
    pending = {}
    for name, content in sources.items():
        destination = project / name
        if paths.strict_workspace():
            destination = paths.category_path(destination, "lean-project")
        if destination.exists():
            if destination.read_bytes() != content:
                raise ValueError(f"Prepared project already contains a different {name}. Choose another directory.")
        else:
            pending[destination] = content
    for destination, content in pending.items():
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_bytes(destination, content)
    return {"project_dir": str(project)}


def run(args):
    action = args.lean_action
    if action in {"prepare", "inspect", "draft", "verify", "submit", "work"}:
        project = args.project_dir or paths.lean_project()
        if action != "prepare" and not (project / "RMFlow").is_dir():
            raise ValueError("Select a prepared legacy Lean project with --project-dir. "
                             "To prepare archived sources, run 'research-multiplier lean prepare SOURCE --project-dir PATH'.")
    if action in {"verify", "submit", "work"}:
        output = args.out or paths.runs() / "lean"
    if action == "prepare":
        result = _prepare(args.source, project)
    elif action == "draft-theory":
        from ..general_lean import propose
        from ..lake_scope import capture_draft

        result = propose(args.packet_id, args.lean, mathlib_project=args.mathlib_project, modules=args.modules)
        capture_draft(result, lean=args.lean, mathlib_project=args.mathlib_project)
    elif action == "check-theory":
        from ..workflows.lean_verification import check_goal

        if bool(args.file) == args.model:
            raise ValueError("Supply a proof document or choose --model.")
        submission = json.loads(args.file.read_text(encoding="utf-8")) if args.file else None
        result = check_goal(args.statement_id, submission, args.out or paths.runs() / "lean", args.lean,
                       retry=args.retry, model=args.model, theorems=args.use_theorem,
                       mathlib_project=args.mathlib_project, refute=args.refute)
    elif action == "replay-formalpedia":
        from research_multiplier.workflows.formalpedia_replay import replay

        if args.file.stat().st_size > 256 * 1024:
            raise ValueError("Use a Formalpedia replay plan of at most 256 KiB.")
        result = replay(args.statement_id, args.snapshot, args.environment,
                        json.loads(args.file.read_text(encoding="utf-8")), lean=args.lean,
                        mathlib_project=args.mathlib_project, output=args.out,
                        retry=args.retry, finish=args.finish)
    elif action == "formalpedia-sources":
        from research_multiplier.storage.postgres import Store
        from research_multiplier.workflows.formalpedia_review import provenance

        with Store() as store:
            result = provenance(store, args.statement_id, include_goals=args.include_goals)
    elif action == "derive":
        from ..workflows.lean_verification import derive_plan

        result = derive_plan(args.statement_id, json.loads(args.file.read_text(encoding="utf-8")),
                        lean=args.lean, mathlib_project=args.mathlib_project, retry=args.retry, output=args.out)
    elif action == "check-source":
        from ..workflows.lean_verification import check_source

        result = check_source(args.statement_id, file=args.file, module=args.module, declaration=args.declaration,
                              lean=args.lean, mathlib_project=args.mathlib_project, retry=args.retry, output=args.out)
    elif action == "reuse-theorem":
        from ..native_reuse import reuse

        result = reuse(args.statement_id, args.version_id, lean=args.lean, mathlib_project=args.mathlib_project,
                       retry=args.retry, output=args.out)
    elif action == "search":
        from ..workflows.lean_search import search

        result = search(args.statement_id, rounds=args.rounds, confirm_public=args.confirm_public,
                        confirm_local=args.confirm_local, authorize_local=args.confirm_local and args.rounds > 0,
                        retry=args.retry, lean=args.lean,
                        mathlib_project=args.mathlib_project, output=args.out)
    elif action == "budget":
        from research_multiplier.policy.lean_budget import inspect

        result = inspect(args.statement_id, limit=args.limit, actor=args.actor, reason=args.reason, history=args.history)
    elif action == "local-theorems":
        from ..workflows.lean_verification import list_lemmas

        result = list_lemmas(args.statement_id)
    elif action == "reductions":
        from research_multiplier.proof.lean_reductions import reductions

        document = json.loads(args.register.read_text(encoding="utf-8")) if args.register else None
        if args.register and document is None:
            raise ValueError("Supply a version 1 reduction document.")
        result = reductions(args.statement_id, document=document, work=args.work, lean=args.lean,
                            mathlib_project=args.mathlib_project, retry=args.retry, output=args.out)
    elif action == "theorems":
        from ..lean_core_reuse import catalog

        result = catalog(args.limit)
    elif action == "export-theorem":
        from ..native_reuse import publish

        result = publish(args.evidence_id, args.run_directory, lean=args.lean, mathlib_project=args.mathlib_project)
    elif action == "blueprint":
        from research_multiplier.proof.lean_blueprint import blueprint

        result = blueprint(json.loads(args.file.read_text(encoding="utf-8")),
                           args.out or paths.runs() / "lean", args.lean,
                           work=args.work, limit=args.limit, retry=args.retry, mathlib_project=args.mathlib_project)
    elif action == "inspect":
        result = LeanProject(project, args.lake).read_report(args.candidate)
    elif action == "draft":
        result = lean_workflow.draft(args.packet_id, project, args.lake)
    elif action == "show":
        result = lean_workflow.show(args.statement_id)
    elif action == "confirm":
        result = lean_workflow.confirm(args.statement_id, args.version, args.checksum, args.actor, args.reason)
    elif action == "verify":
        result = lean_workflow.verify(args.statement_id, project, args.candidate, output, args.lake)
    elif action == "submit":
        if args.file and args.model:
            raise ValueError("Choose either a candidate file or an authored candidate.")
        if args.retry and not args.model:
            raise ValueError("The submission retry option requires --model.")
        document = json.loads(args.file.read_text(encoding="utf-8")) if args.file else None
        result = lean_jobs.submit(args.statement_id, document, args.target, output, project,
                                  args.lake, model=args.model, retry=args.retry)
    elif action == "work":
        result = lean_jobs.work(args.job_id, output, project, args.lake, retry=args.retry)
    else:
        result = lean_workflow.review(args.check_id, args.action, args.actor, args.reason)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=_encode))
    if action == "blueprint" and args.work and any(node["status"] == "proof_failed" for node in result["nodes"]):
        return 2
    return 2 if isinstance(result, dict) and result.get("accepted") is False else 0
