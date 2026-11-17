"""Prepare and confirm a single set of scoped research notes."""

from datetime import datetime
import json
from pathlib import Path
from uuid import UUID

from research_multiplier.workflows import intake
from research_multiplier.storage.postgres import Store


from ._command import register
from ._output import json_default as _encode


def add_commands(commands):
    parser = commands.add_parser("intake", help="Review and consolidate starting research notes.")
    parser.set_defaults(_handler=run)
    actions = parser.add_subparsers(dest="intake_action", required=True)
    proposal = register(actions, "propose-target", help="Use a configured model to draft a target for review.")
    proposal.add_argument("file", type=Path)
    disclosure = proposal.add_mutually_exclusive_group(required=True)
    disclosure.add_argument("--confirm-public", action="store_true",
                            help="Confirm public material can be sent to the configured model.")
    disclosure.add_argument("--confirm-local", action="store_true",
                            help="Use the trusted local model on restricted material.")
    preview = register(actions, "preview-target", help="Read the exact target and its review hash.")
    freeze = register(actions, "freeze-target", help="Freeze the exact target before reviewing research inputs.")
    for command in (preview, freeze):
        source = command.add_mutually_exclusive_group(required=True)
        source.add_argument("file", type=Path, nargs="?")
        source.add_argument("--from-proposal", metavar="SHA256", help="Use the target from an archived proposal.")
    freeze.add_argument("--hash", dest="checksum", required=True)
    freeze.add_argument("--actor", required=True)
    freeze.add_argument("--reason", required=True)
    selected = register(actions, "target", help="Show the frozen target and its recorded starting basis binding.")
    selected.add_argument("target_id", type=UUID)
    draft = register(actions, "draft", help="Compare proposed starting items with approved versions.")
    draft.add_argument("file", type=Path)
    draft.add_argument("--target", type=UUID, metavar="TARGET_ID", help="Use a previously frozen target.")
    extraction = draft.add_mutually_exclusive_group()
    extraction.add_argument("--model", action="store_true", help="Extract candidates from explicitly public notes.")
    extraction.add_argument("--confirm-local", action="store_true",
                            help="Extract candidates from restricted notes using the trusted local model.")
    show = register(actions, "show", help="Show the stored notes, comparison and outstanding decisions.")
    show.add_argument("intake_id", type=UUID)
    confirm = register(actions, "confirm", help="Confirm this exact basis and start a research goal frame.")
    confirm.add_argument("intake_id", type=UUID)
    confirm.add_argument("--hash", dest="checksum", required=True)
    confirm.add_argument("--decisions", type=Path, help="Item decisions as a JSON object.")
    confirm.add_argument("--actor", required=True)
    confirm.add_argument("--reason", required=True)


def _read_file(path, limit):
    with path.open("rb") as stream:
        return stream.read(limit + 1)


def _target_input(args):
    if args.from_proposal:
        from research_multiplier.workflows import target_draft

        return target_draft.archived_target(args.from_proposal)
    return _read_file(args.file, intake.MAX_TARGET_BYTES)


def run(args):
    with Store() as store:
        if args.intake_action == "propose-target":
            from research_multiplier.workflows import target_draft

            result = target_draft.propose(_read_file(args.file, target_draft.MAX_INPUT_BYTES),
                                          confirm_local=args.confirm_local, store=store)
            if result["target"] is not None:
                result["review_command"] = (
                    "research-multiplier intake preview-target --from-proposal " + result["record"]["sha256"]
                )
        elif args.intake_action == "preview-target":
            result = intake.preview_target(store, _target_input(args))
        elif args.intake_action == "freeze-target":
            result = intake.freeze_target(store, _target_input(args), args.checksum, args.actor, args.reason)
        elif args.intake_action == "target":
            result = intake.target(store, args.target_id)
        elif args.intake_action == "draft":
            result = intake.draft(store, _read_file(args.file, intake.MAX_NOTES_BYTES),
                                  model=args.model, target_id=args.target, confirm_local=args.confirm_local)
        elif args.intake_action == "show":
            result = intake.show(store, args.intake_id)
        else:
            decisions = {}
            if args.decisions:
                raw = _read_file(args.decisions, 650_000)
                if len(raw) > 650_000:
                    raise ValueError("Intake decisions are limited to 650 KB.")
                decisions = json.loads(raw)
            result = intake.confirm(store, args.intake_id, args.checksum, decisions, args.actor, args.reason)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=_encode))
    return 0
