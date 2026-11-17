"""CLI for approved dependency-aware research plans."""

from datetime import datetime
import json
from pathlib import Path
from time import sleep
from uuid import UUID

from research_multiplier.workflows import research_plans

from research_multiplier.storage.postgres import configured_worker_roles

from ._command import register
from ._output import json_default as _encode


def _worker_roles(_args):
    return configured_worker_roles("scheduler")


def add_commands(commands):
    plan = commands.add_parser("plan", help="Create and execute a durable dependency-aware research plan.")
    plan.set_defaults(_handler=run)
    actions = plan.add_subparsers(dest="plan_action", required=True)
    create = register(actions, "create", help="Register bounded actions against reviewed packets.")
    create.add_argument("file", type=Path)
    show = register(actions, "show", help="Show the route, its ready steps, and their attempts.")
    show.add_argument("id", type=UUID)
    work = register(actions, "work", roles=_worker_roles, help="Execute ready steps and return when blocked or the limit is reached.")
    work.add_argument("id", type=UUID)
    work.add_argument("--limit", type=int, default=16, help="Maximum attempts to start in this invocation.")
    worker = register(actions, "worker", roles=_worker_roles, help="Execute ready steps across specified research plans.")
    worker.add_argument("--plan", required=True, type=UUID, action="append", help="Repeat to include additional plans.")
    worker.add_argument("--limit", type=int, default=16, help="Maximum attempts to start per cycle.")
    worker.add_argument("--parallel", type=int, default=2, help="Local execution slots across the plans.")
    worker.add_argument("--watch", type=int, metavar="SECONDS", help="Poll repeatedly at this interval.")
    cancel = register(actions, "cancel", help="Cancel unfinished steps and fence their leases.")
    cancel.add_argument("id", type=UUID)
    cancel.add_argument("--reason", required=True)


def run(args):
    if args.plan_action == "create":
        result = research_plans.create(json.loads(args.file.read_bytes()))
    elif args.plan_action == "show":
        result = research_plans.show(args.id)
    elif args.plan_action == "work":
        result = research_plans.work(args.id, args.limit)
    elif args.plan_action == "worker":
        if args.watch is not None and not 1 <= args.watch <= 60:
            raise ValueError("Choose a polling interval between 1 and 60 seconds.")
        if args.watch is not None:
            try:
                while True:
                    cycle = research_plans.work_group(args.plan, args.limit, args.parallel)
                    if cycle["attempts_started"] or cycle["worker_errors"]:
                        print(json.dumps(cycle, ensure_ascii=False, default=_encode), flush=True)
                    sleep(args.watch)
            except KeyboardInterrupt:
                return 0
        result = research_plans.work_group(args.plan, args.limit, args.parallel)
    else:
        result = research_plans.cancel(args.id, args.reason)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=_encode))
    return 0
