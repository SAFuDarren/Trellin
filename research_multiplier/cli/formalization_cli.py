"""Commands for reviewing the statement and premises of a proof."""

from datetime import datetime
import json
from pathlib import Path

from research_multiplier.workflows import formalization
from research_multiplier.storage import paths
from research_multiplier.domain_packages import production_domains


from . import load_json
from ._command import register, HUMAN, PROVE, VERIFIER, Output
from ._output import json_default as _encode


def add_commands(commands):
    command = commands.add_parser("formalization", help="Draft and confirm the exact statement and premises.")
    command.set_defaults(_handler=lambda args: run(args, load_json))
    actions = command.add_subparsers(dest="alignment_action", required=True)

    draft = register(actions, "draft", help="Store a review packet for an exact public instance.")
    draft.add_argument("input", type=Path)
    draft.add_argument("--domain", default=production_domains.DEFAULT_DOMAIN, help="Registered production domain name.")
    draft.add_argument("--title", required=True)
    draft.add_argument("--clarify", type=Path, help="JSON mapping each unresolved item ID to one question.")

    explore_draft = register(actions, "explore-draft", help="Store a public, goal-free exploration basis.")
    explore_draft.add_argument("document", type=Path)

    explore = register(actions, "explore", roles=lambda args: (*HUMAN, *VERIFIER) if args.model else HUMAN, help="Show the approved basis and a conjecture to consider.")
    explore.add_argument("packet_id")
    explore.add_argument("--model", action="store_true", help="Request analysis from the configured exploration model.")

    choose = register(actions, "choose", help="Draft a selected conjecture for a new review.")
    choose.add_argument("packet_id")
    choose.add_argument("--candidate", required=True)
    choose.add_argument("--version", type=int, required=True)
    choose.add_argument("--hash", dest="checksum", required=True)

    show = register(actions, "show", help="Read the review packet and its open questions.")
    show.add_argument("packet_id")

    confirm = register(actions, "confirm", help="Confirm the exact packet and its listed open questions.")
    confirm.add_argument("packet_id")
    confirm.add_argument("--version", type=int, required=True)
    confirm.add_argument("--hash", dest="checksum", required=True)
    confirm.add_argument("--resolve", action="append", default=[], metavar="ITEM")
    confirm.add_argument("--actor", required=True)
    confirm.add_argument("--reason", required=True)

    prove = register(actions, "prove", roles=PROVE, outputs=(Output("out", "runs/claims"),), help="Run the proof against the confirmed packet.")
    prove.add_argument("packet_id")
    prove.add_argument("--out", type=Path, help="Folder for run artifacts (default: data directory).")
    prove.add_argument("--strategy", choices=("program", "model"),
                       help="Select a proof strategy on the first run. Defaults to program.")
    prove.add_argument("--retry", action="store_true", help="Retry a proof after resolving an open question.")
    prove.add_argument("--certificate", type=Path, help="Submit a domain's exact certificate for independent checking.")

    resolve = register(actions, "resolve", help="Resolve a new question while confirming the frozen meaning.")
    resolve.add_argument("request_id")
    resolve.add_argument("--actor", required=True)
    resolve.add_argument("--reason", required=True)


def run(args, load_json):
    if args.alignment_action == "draft":
        result = formalization.draft(load_json(args.input), args.title, domain=args.domain,
                                     ambiguities=load_json(args.clarify) if args.clarify else None)
    elif args.alignment_action in {"explore-draft", "explore", "choose"}:
        from research_multiplier.workflows import exploration

        if args.alignment_action == "explore-draft":
            result = exploration.draft(load_json(args.document))
        elif args.alignment_action == "explore":
            result = exploration.explore(args.packet_id, use_model=args.model)
        else:
            result = exploration.choose(args.packet_id, args.version, args.checksum, args.candidate)
    elif args.alignment_action == "show":
        result = formalization.show(args.packet_id)
    elif args.alignment_action == "confirm":
        result = formalization.confirm(args.packet_id, args.version, args.checksum,
                                       args.resolve, args.actor, args.reason)
    elif args.alignment_action == "resolve":
        result = {"id": formalization.resolve(args.request_id, args.actor, args.reason)}
    else:
        from ..production import run_formalized

        certificate = load_json(args.certificate) if args.certificate else None
        if args.certificate and not isinstance(certificate, dict):
            raise ValueError("Expected a JSON certificate object.")
        directory, outcome = run_formalized(args.packet_id, args.out or paths.claim_runs(),
                                            retry=args.retry, strategy=args.strategy, certificate=certificate)
        result = {"directory": str(directory), "evidence_id": outcome["evidence_id"],
                  "claim_status": outcome["claim_status"], "human_review": outcome["human_review"]}
    print(json.dumps(result, indent=2, ensure_ascii=False, default=_encode))
    return 0
