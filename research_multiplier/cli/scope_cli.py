"""Commands for reviewing observed proof scope against a frozen packet."""

import json
from pathlib import Path
from uuid import UUID

from research_multiplier.policy import scope_review
from research_multiplier.storage.postgres import Store


from . import load_json
from ._command import register, VERIFIER


def add_commands(commands):
    scope = commands.add_parser("scope", help="Compare structured statements or independently extract proof scope.")
    scope.set_defaults(_handler=lambda args: run(args, load_json))
    actions = scope.add_subparsers(dest="scope_action", required=True)
    statement = register(actions, "compare", help="Compare a frozen packet with an observed structured statement.")
    statement.add_argument("packet_id", type=UUID)
    statement.add_argument("observed", type=Path)
    lean = register(actions, "lean-statement", help="Compare a Lean statement with its reviewed research scope.")
    lean.add_argument("statement_id", type=UUID)
    lean.add_argument("--backtranslate-public", action="store_true",
                      help="Confirm public scope and request a blind translation of the formal statement.")
    extract = register(actions, "blind", roles=VERIFIER, help="Send only a public derivation to the configured independent auditor.")
    extract.add_argument("evidence_id", type=UUID)
    extract.add_argument("--confirm-public", action="store_true")
    extract.add_argument("--run-directory", type=Path)
    extract.add_argument("--timeout", type=int, default=90)
    listing = register(actions, "list", help="List recorded blind extractions for a proof.")
    listing.add_argument("evidence_id", type=UUID)
    show = register(actions, "show", help="Read an archived extraction by ID.")
    show.add_argument("extraction_id", type=UUID)


def run(args, load_json):
    with Store() as store:
        action = args.scope_action
        if action == "compare":
            frozen = scope_review.expected_scope(store, args.packet_id)
            result = scope_review.compare(frozen["packet"], load_json(args.observed))
            result.update(packet_id=str(frozen["id"]), packet_sha256=frozen["packet_sha256"])
        elif action == "lean-statement":
            result = scope_review.lean_statement(store, args.statement_id, backtranslate=args.backtranslate_public)
        elif action == "blind":
            result = scope_review.blind(
                store, args.evidence_id, confirm_public=args.confirm_public,
                run_directory=args.run_directory, timeout=args.timeout,
            )
        elif action == "list":
            result = scope_review.extractions(store, args.evidence_id)
        else:
            result = scope_review.read_extraction(store, args.extraction_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
