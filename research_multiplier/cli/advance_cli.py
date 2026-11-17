"""Research progress preparation, approval and durable worker entry point."""

from datetime import datetime
import json
from pathlib import Path
from time import sleep
from uuid import UUID

from research_multiplier.workflows import research_progress
from research_multiplier.workflows.runtime import VerificationQueueError
from research_multiplier.proof.lean_watch import LakeWatch
from research_multiplier.storage.postgres import DatabaseOperationError, Store, configured_worker_roles


from . import load_json
from ._command import VERIFIER, VERIFY, register
from ._output import json_default as _encode


def _worker_roles(args):
    if args.stage == "verifier":
        return VERIFIER
    return configured_worker_roles(*(VERIFY if args.stage == "combined" else ("scheduler",)))


def _preflight_worker_roles(args):
    # A watching worker verifies roles inside its reconnect loop.
    return () if args.watch is not None else _worker_roles(args)


def add_commands(commands):
    advance = commands.add_parser("advance", help="Advance an explicitly approved frozen research scope.")
    advance.set_defaults(_handler=lambda args: run(args, load_json))
    actions = advance.add_subparsers(dest="advance_action", required=True)
    route = register(actions, "route", help="Suggest a program-first policy for a frozen packet.")
    route.add_argument("packet", type=UUID)
    propose = register(actions, "propose", help="Propose a metric, allowed actions, and budget.")
    source = propose.add_mutually_exclusive_group(required=True)
    source.add_argument("file", type=Path, nargs="?")
    source.add_argument("--for-packet", type=UUID, help="Propose the recommended route for this packet.")
    propose.add_argument("--lean-model-rounds", type=int, choices=range(9),
                         help="Allow this many model rounds per confirmed Lean inquiry.")
    propose.add_argument("--lean-route-parallelism", type=int, choices=(1, 2, 3),
                         help="Maximum simultaneous proof routes under the signed total call allowance.")
    propose.add_argument("--lean-route-stall-limit", type=int, choices=(1, 2, 3),
                         help="Consecutive checkpoints without proof-duty progress before pausing a route.")
    propose.add_argument("--confirm-local", action="store_true",
                         help="Bind these rounds to the exact locally configured private Lean model.")
    approve = register(actions, "approve", help="Approve the exact hashed progress policy.")
    approve.add_argument("id", type=UUID)
    approve.add_argument("--hash", required=True)
    approve.add_argument("--actor", required=True)
    approve.add_argument("--reason", required=True)
    status = register(actions, "status", help="Read current progress, recorded rounds and stopping causes.")
    status.add_argument("id", type=UUID, nargs="?")
    metrics = register(actions, "metrics", help="Measure completed Lean passes, model usage and pending review.")
    metrics.add_argument("id", type=UUID)
    metrics.add_argument("--details", action="store_true", help="Include model metrics per reserved call.")
    stop = register(actions, "stop", help="Stop a policy and cancel its unfinished plan steps.")
    stop.add_argument("id", type=UUID)
    stop.add_argument("--reason", required=True)
    worker = register(actions, "worker", roles=_preflight_worker_roles, help="Take a bounded pass over all eligible policies in this project database.")
    worker.add_argument("--stage", choices=("combined", "scheduler", "verifier"), default="combined")
    worker.add_argument("--limit", type=int, default=16)
    worker.add_argument("--parallel", type=int, default=2, help="Local execution slots across the selected plans and their proof routes.")
    worker.add_argument("--policy", type=UUID, action="append", help="Restrict work to specified policies. Repeat to add a policy.")
    worker.add_argument("--watch", type=int, metavar="SECONDS", help="Poll repeatedly at this interval.")
    worker.add_argument("--lean", help="Path to the pinned Lean compiler.")
    worker.add_argument("--mathlib-project", type=Path, help="Default Lake project for this worker.")
    worker.add_argument("--lake-map", type=Path, help="JSON mapping Lean statement IDs to their Lake project folders.")


def _cycle(args, lake_watch):
    return research_progress.advance_cycle(args.stage, args.limit, args.policy, args.parallel,
                                           lean=args.lean, lake_watch=lake_watch)


def _watch(args, lake_watch):
    delay = args.watch
    connect = True
    try:
        while True:
            try:
                if connect:
                    with Store() as store:
                        store.verify_runtime_roles(roles=_worker_roles(args))
                    connect = False
                cycle = _cycle(args, lake_watch)
            except (DatabaseOperationError, VerificationQueueError) as error:
                if not error.retryable:
                    raise
                lake_watch.reset_observations()
                status = "verification_retry" if isinstance(error, VerificationQueueError) else "database_retry"
                print(json.dumps({"status": status, "reason": str(error), "next_retry_seconds": delay}), flush=True)
                connect = True
                sleep(delay)
                delay = min(delay * 2, 60)
                continue
            if cycle["attempts_started"] or cycle["observations"] or cycle["errors"]:
                print(json.dumps(cycle, ensure_ascii=False, default=_encode), flush=True)
            delay = args.watch
            sleep(delay)
    except KeyboardInterrupt:
        return 0


def run(args, load_json=None):
    action = args.advance_action
    if action == "route":
        result = research_progress.route(args.packet)
    elif action == "propose":
        if args.for_packet is None:
            document = load_json(args.file) if load_json else json.loads(args.file.read_bytes())
        else:
            choice = research_progress.route(args.for_packet)
            document = choice["suggested_policy"]
            if document is None:
                raise ValueError(choice["reason"])
        if args.lean_model_rounds is not None:
            if document.get("actions") != ["lean"]:
                raise ValueError("Set model rounds only for a Lean advancement policy.")
            document = {**document, "lean_model_rounds": args.lean_model_rounds}
        for option in ("lean_route_parallelism", "lean_route_stall_limit"):
            value = getattr(args, option)
            if value is not None:
                if document.get("actions") != ["lean"]:
                    raise ValueError("Set route controls only for a Lean advancement policy.")
                document = {**document, option: value}
        if args.confirm_local:
            if document.get("actions") != ["lean"]:
                raise ValueError("Local model authorization belongs to a Lean policy.")
            document = {**document, "lean_model_scope": "local"}
        result = research_progress.propose(document)
    elif action == "approve":
        result = research_progress.approve(args.id, args.hash, args.actor, args.reason)
    elif action == "status":
        result = research_progress.status(args.id)
    elif action == "metrics":
        from research_multiplier.workflows.research_metrics import inquiry

        result = inquiry(args.id, details=args.details)
    elif action == "stop":
        result = research_progress.stop(args.id, args.reason)
    else:
        if args.watch is not None and not 1 <= args.watch <= 60:
            raise ValueError("Choose a polling interval between 1 and 60 seconds.")
        lake_watch = LakeWatch(args.mathlib_project, args.lake_map, lean=args.lean)
        if args.watch is not None:
            return _watch(args, lake_watch)
        result = _cycle(args, lake_watch)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=_encode))
    return 0
