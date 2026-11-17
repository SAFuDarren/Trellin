"""Commands for collecting and comparing sealed evaluation responses."""

from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
from uuid import UUID

from research_multiplier import eval_component, eval_injections, eval_runner, evaluation
from research_multiplier.evaluations import evolution, lean_shadow, lean_shadow_campaign
from research_multiplier.proof.lean_search_components import VERSIONS as LEAN_ABLATIONS
from research_multiplier.evaluations.lean_shadow_build import pair as paired_installations
from research_multiplier.storage.postgres import Store
from research_multiplier.workflows.lean_search import SEARCH_STRATEGIES


from ._command import register, HUMAN, VERIFIER
from ._output import json_default as _encode


def _lean_options(command):
    for side in lean_shadow.SIDES:
        command.add_argument(f"--{side}-install", type=Path,
                             help=f"Directory containing the trusted installed {side} wheel.")
    command.add_argument("--baseline-profile", help="Use a named model profile for the baseline side.")
    command.add_argument("--candidate-profile", help="Use a named model profile for the candidate side.")
    command.add_argument("--baseline-strategy", choices=SEARCH_STRATEGIES, default="ranked")
    command.add_argument("--candidate-strategy", choices=SEARCH_STRATEGIES, default="ranked")
    command.add_argument("--confirm-public", action="store_true")
    command.add_argument("--rounds", type=int, default=2)
    command.add_argument("--side-timeout", type=int, default=300, metavar="SECONDS")
    command.add_argument("--lean")
    command.add_argument("--mathlib-project", type=Path)


def add_commands(commands):
    group = commands.add_parser("eval", help="Collect and compare responses on sealed evaluation cases.")
    group.set_defaults(_handler=run)
    actions = group.add_subparsers(dest="eval_action", required=True)
    keys = register(actions, "keys", roles=(), workspace=False, help="Print case IDs and frozen input hashes for response files.")
    keys.add_argument("cases", type=Path)
    inject = register(actions, "inject", roles=(), help="Run controlled mutations against the independent checkers.")
    inject.add_argument("cases", type=Path)
    collect = register(actions, "collect", roles=(), help="Collect a bounded program or model run on one evaluation split.")
    collect.add_argument("development", type=Path)
    collect.add_argument("holdout", type=Path)
    collect.add_argument("--split", choices=("development", "holdout"), default="development")
    collect.add_argument("--responder", choices=("program", "model"), required=True)
    collect.add_argument("--confirm-public", action="store_true", help="Authorize sending public contracts to the model.")
    collect.add_argument("--max-cases", type=int, default=32)
    collect.add_argument("--timeout", type=int, default=60)
    collect.add_argument("--max-output-tokens", type=int, default=4096)
    compare = register(actions, "compare", roles=lambda args: VERIFIER if args.record else (), help="Dry-run an independent comparison on development or holdout cases.")
    for field in ("development", "holdout", "baseline", "candidate"):
        compare.add_argument(field, type=Path)
    compare.add_argument("--split", choices=("development", "holdout"), default="holdout")
    compare.add_argument("--component", type=Path, help="Bind a one-component ablation or challenger manifest.")
    compare.add_argument("--record", action="store_true", help="Record the summary through the verifier role.")
    reproduce = register(actions, "reproduce", roles=(), help="Repeat a comparison from its archived bytes.")
    reproduce.add_argument("sha256")
    status = register(actions, "status", help="Read the recorded comparison and human recommendation.")
    status.add_argument("report_id", type=UUID)
    approve = register(actions, "approve", help="Record a human recommendation for an eligible holdout report.")
    approve.add_argument("report_id", type=UUID)
    approve.add_argument("--hash", required=True, dest="checksum")
    approve.add_argument("--actor", required=True)
    approve.add_argument("--reason", required=True)
    shadow = register(actions, "shadow", roles=VERIFIER, help="Run the current and proposed production prover on a frozen holdout.")
    shadow.add_argument("development", type=Path)
    shadow.add_argument("holdout", type=Path)
    shadow.add_argument("--candidate-profile", required=True)
    shadow.add_argument("--confirm-public", action="store_true")
    shadow.add_argument("--max-cases", type=int, default=32)
    shadow.add_argument("--timeout", type=int, default=evolution.production.MODEL_TIMEOUT)
    live = register(actions, "shadow-live", roles=VERIFIER, help="Try both prover profiles on exact public frozen claims from this workspace.")
    live.add_argument("claim_ids", nargs="+", type=UUID)
    live.add_argument("--candidate-profile", required=True)
    live.add_argument("--confirm-public", action="store_true")
    live_status = register(actions, "shadow-live-status", help="Read the known and unreviewed outcomes of a live trial.")
    live_status.add_argument("trial_id", type=UUID)
    lean = register(actions, "shadow-lean", roles=VERIFIER, help="Compare Lean search in two isolated public workspaces.")
    lean.add_argument("statement_id", type=UUID)
    # These paths are checked against their own database bindings by lean_shadow.
    lean.add_argument("baseline_workspace")
    lean.add_argument("candidate_workspace")
    _lean_options(lean)
    campaign = register(actions, "shadow-lean-campaign", roles=VERIFIER, help="Evaluate ordered Lean trajectories across restored pairs.")
    campaign.add_argument("manifest", type=Path)
    campaign.add_argument("--max-model-calls", type=int, default=0)
    campaign.add_argument("--campaign-timeout", type=int, default=900, metavar="SECONDS")
    _lean_options(campaign)
    campaign.add_argument("--corpus-sha256", help="Bind the experiment to one split of a sealed Lean evaluation corpus.")
    campaign.add_argument("--ablate", choices=LEAN_ABLATIONS,
                          help="Omit one scaffolding component on the candidate side of a sealed campaign.")
    corpus = register(actions, "lean-corpus-seal", roles=(*HUMAN, *VERIFIER), help="Review and freeze disjoint Lean development and holdout inputs.")
    corpus.add_argument("file", type=Path)
    corpus.add_argument("--actor", required=True)
    corpus.add_argument("--reason", required=True)
    corpus.add_argument("--lean")
    corpus.add_argument("--mathlib-project", type=Path)
    lean_status = register(actions, "shadow-lean-status", roles=(), help="Read a Lean search comparison by its report hash.")
    lean_status.add_argument("sha256")
    lean_activate = register(actions, "lean-activate", roles=(*HUMAN, *VERIFIER), help="Approve and apply an eligible Lean holdout result.")
    lean_activate.add_argument("checksum")
    lean_activate.add_argument("review", type=Path)
    lean_rollback = register(actions, "lean-rollback", help="Restore a previous approved Lean search configuration.")
    lean_rollback.add_argument("event_id", type=UUID)
    lean_reset = register(actions, "lean-reset-after-upgrade", help="Authorize the installed default Lean route after an upgrade.")
    lean_reset.add_argument("event_id", type=UUID)
    register(actions, "lean-active", help="Inspect the approved Lean search configuration and history.")
    shadow_status = register(actions, "shadow-status", help="Inspect a paired experiment and its route decisions.")
    shadow_status.add_argument("experiment_id", type=UUID)
    review = register(actions, "shadow-review", help="Record actual review minutes for one side of a frozen test case.")
    review.add_argument("experiment_id", type=UUID)
    review.add_argument("case_id")
    review.add_argument("side", choices=("baseline", "candidate"))
    review.add_argument("--minutes", required=True, type=Decimal)
    review.add_argument("--actor", required=True)
    activate = register(actions, "activate", help="Apply a human-reviewed experimental production profile.")
    activate.add_argument("experiment_id", type=UUID)
    activate.add_argument("--hash", required=True, dest="checksum")
    activate.add_argument("--live-trial", required=True, type=UUID)
    activate.add_argument("--live-hash", required=True)
    rollback = register(actions, "rollback", help="Restore the previous exact production model configuration.")
    rollback.add_argument("event_id", type=UUID)
    for decision in (activate, rollback, lean_activate, lean_rollback, lean_reset):
        decision.add_argument("--actor", required=True)
        decision.add_argument("--reason", required=True)


def _load(path):
    return json.loads(path.read_bytes())


def _result(args):
    action = args.eval_action
    if action == "keys":
        return {"subjects": evaluation.keys(_load(args.cases))}
    if action == "inject":
        return eval_injections.run(_load(args.cases))
    if action == "collect":
        return eval_runner.collect(
            _load(args.development), _load(args.holdout), args.split, args.responder,
            confirm_public=args.confirm_public, max_cases=args.max_cases,
            timeout_seconds=args.timeout, max_output_tokens=args.max_output_tokens,
        )
    if action == "shadow":
        return evolution.shadow(_load(args.development), _load(args.holdout), args.candidate_profile,
                                confirm_public=args.confirm_public, max_cases=args.max_cases,
                                timeout_seconds=args.timeout)
    if action == "shadow-live":
        return evolution.shadow_live(args.claim_ids, args.candidate_profile, confirm_public=args.confirm_public)
    if action == "shadow-live-status":
        return evolution.live_status(args.trial_id)
    if action == "lean-corpus-seal":
        from research_multiplier.evaluations import lean_eval_corpus

        return lean_eval_corpus.seal(_load(args.file), args.actor, args.reason,
                                     lean=args.lean, mathlib_project=args.mathlib_project)
    if action in {"shadow-lean", "shadow-lean-campaign"}:
        options = {name: getattr(args, name) for name in (
            "baseline_profile", "confirm_public", "rounds", "side_timeout", "lean", "mathlib_project",
            "baseline_strategy", "candidate_strategy",
        )}
        with paired_installations(args.baseline_install, args.candidate_install) as builds:
            if action == "shadow-lean":
                return lean_shadow.shadow(args.statement_id, args.baseline_workspace, args.candidate_workspace,
                                          args.candidate_profile, builds=builds, **options)
            return lean_shadow_campaign.campaign(
                _load(args.manifest), args.manifest.resolve().parent, args.candidate_profile,
                max_model_calls=args.max_model_calls, campaign_timeout=args.campaign_timeout,
                corpus_sha256=args.corpus_sha256, builds=builds, ablation=args.ablate, **options,
            )
    if action == "shadow-lean-status":
        return lean_shadow.status(args.sha256)
    if action in {"lean-activate", "lean-rollback", "lean-reset-after-upgrade", "lean-active"}:
        from research_multiplier.evaluations import lean_evolution

        if action == "lean-activate":
            return lean_evolution.activate(args.checksum, _load(args.review), args.actor, args.reason)
        if action == "lean-rollback":
            return lean_evolution.rollback(args.event_id, args.actor, args.reason)
        if action == "lean-reset-after-upgrade":
            return lean_evolution.reset_after_upgrade(args.event_id, args.actor, args.reason)
        return lean_evolution.status()
    if action == "shadow-status":
        return evolution.status(args.experiment_id)
    if action == "shadow-review":
        return evolution.record_review(args.experiment_id, args.case_id, args.side, args.minutes, args.actor)
    if action == "activate":
        return evolution.activate(args.experiment_id, args.checksum, args.live_trial, args.live_hash,
                                  args.actor, args.reason)
    if action == "rollback":
        return evolution.rollback(args.event_id, args.actor, args.reason)
    if action == "reproduce":
        _, replayed = eval_component.reproduce(args.sha256)
        return {"verified": replayed}
    if action == "compare":
        inputs = [_load(args.development), _load(args.holdout), _load(args.baseline), _load(args.candidate)]
        if args.component:
            body, artifact = eval_component.compare(*inputs, args.split, _load(args.component))
        else:
            body, artifact = evaluation.compare(*inputs, args.split)
        result = eval_component.summary(body, artifact)
        if args.record:
            with Store() as store:
                recorded = store.query("verifier", "SELECT rm.register_eval_report(%s) AS id", store.json(result))[0]
            result["report_id"] = recorded["id"]
        return result
    with Store() as store:
        rows = store.query("human", "SELECT * FROM rm.evaluation_report_status WHERE id = %s", args.report_id)
        if not rows:
            raise ValueError("No evaluation report matches this ID.")
        if action == "status":
            return rows[0]
        if args.checksum != rows[0]["report_sha256"]:
            raise ValueError("Review the exact evaluation artifact hash before approving.")
        reproduced, artifact = eval_component.reproduce(args.checksum)
        if eval_component.summary(reproduced, artifact) != rows[0]["summary"]:
            raise ValueError("The registered report summary differs from its verified artifact.")
        return store.query("human", "SELECT rm.approve_eval_report(%s,%s,%s,%s) AS id",
                           args.report_id, args.checksum, args.actor, args.reason)[0]


def run(args):
    result = _result(args)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=_encode))
    if args.eval_action == "inject" and result["count"] != result["detected"]:
        return 1
    return 0
