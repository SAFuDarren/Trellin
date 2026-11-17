"""Review goal frames, compare conjectures and decide scoped assumptions."""

from datetime import datetime
import json
from pathlib import Path
from uuid import UUID

from research_multiplier.storage.postgres import Store


from ._command import register, VERIFIER
from ._output import json_default as _encode


def _review(parser):
    parser.add_argument("--actor", required=True)
    parser.add_argument("--reason", required=True)


def add_commands(commands):
    goal = commands.add_parser("goal", help="Confirm a research basis and select an exact conjecture.")
    goal.set_defaults(_handler=run)
    actions = goal.add_subparsers(dest="goal_action", required=True)
    draft = register(actions, "draft", help="Declare a goal frame using approved version IDs.")
    draft.add_argument("file", type=Path)
    draft.add_argument("--revision-of", type=UUID)
    show = register(actions, "show", help="Read a frame, its exact basis, alternatives and condition questions.")
    show.add_argument("goal_id", type=UUID)
    confirm = register(actions, "confirm", help="Confirm the reviewed frame and its frozen basis.")
    confirm.add_argument("goal_id", type=UUID)
    confirm.add_argument("--hash", dest="checksum", required=True)
    _review(confirm)
    propose = register(actions, "propose", help="Import a conjecture with its provenance, without selecting it.")
    propose.add_argument("goal_id", type=UUID)
    propose.add_argument("file", type=Path)
    suggest = register(actions, "suggest", help="Ask the configured model for alternatives on a confirmed frame.")
    suggest.add_argument("goal_id", type=UUID)
    suggest.add_argument("--confirm-local", action="store_true", help="Use the trusted local model on a restricted frame.")

    discuss = register(actions, "discuss", help="Explore natural-language targets before formalizing one.")
    discuss.add_argument("goal_id", type=UUID)
    discuss.add_argument("file", type=Path)

    discussion = register(actions, "discussion", help="Read the latest discussion on the current reviewed frame.")
    discussion.add_argument("goal_id", type=UUID)
    formalize = register(actions, "formalize", help="Prepare an archived discussion candidate for selection.")
    formalize.add_argument("goal_id", type=UUID)
    formalize.add_argument("discussion_sha256", help="The record.sha256 printed by goal discuss.")
    formalize.add_argument("candidate_index", type=int, help="The candidate's one-based position in the discussion.")
    for command in (discuss, formalize):
        disclosure = command.add_mutually_exclusive_group(required=True)
        disclosure.add_argument("--confirm-public", action="store_true")
        disclosure.add_argument("--confirm-local", action="store_true")
    select = register(actions, "select", help="Confirm and freeze the exact chosen conjecture.")
    select.add_argument("conjecture_id", type=UUID)
    select.add_argument("--hash", dest="checksum", required=True)
    _review(select)
    condition = register(actions, "condition", help="Record a possible missing assumption without adopting it.")
    condition.add_argument("goal_id", type=UUID)
    condition.add_argument("file", type=Path)
    reopen = register(actions, "reopen-condition", help="Open a new review for a condition with stale supporting evidence.")
    reopen.add_argument("request_id", type=UUID)
    reopen.add_argument("--actor", required=True)
    reopen.add_argument("--reason", required=True)
    decide = register(actions, "decide-condition", help="Close one condition question with its explicit disposition.")
    decide.add_argument("request_id", type=UUID)
    decide.add_argument("action", choices=("reaffirm", "reject", "revise", "derived"))
    decide.add_argument("--version", type=UUID, help="The exact matching hypothesis for a reaffirmation or revision.")
    decide.add_argument("--evidence", type=UUID, help="Independent exact evidence for a derived condition.")
    _review(decide)
    record = register(actions, "record-derivation", roles=VERIFIER, help="Discharge a route condition with independent exact evidence.")
    record.add_argument("request_id", type=UUID)
    record.add_argument("evidence_id", type=UUID)
    local = register(actions, "record-local-derivation", roles=VERIFIER,
                     help="Discharge a route condition with a checked local Lean lemma.")
    local.add_argument("request_id", type=UUID)
    local.add_argument("lemma_id", type=UUID)


def _show(store, goal_id):
    frames = store.query("human", "SELECT * FROM rm.research_goal_status WHERE id = %s", goal_id)
    if not frames:
        raise ValueError("Research goal frame does not exist.")
    return {
        "frame": frames[0],
        "basis": store.query("human", """
            SELECT * FROM rm.research_goal_basis WHERE goal_id = %s ORDER BY position
        """, goal_id),
        "conjectures": store.query("human", """
            SELECT * FROM rm.research_conjectures WHERE goal_id = %s ORDER BY proposed_at, id
        """, goal_id),
        "conditions": store.query("human", """
            SELECT * FROM rm.research_condition_status WHERE goal_id = %s ORDER BY requested_at, id
        """, goal_id),
    }


def _document(store, path):
    return store.json(json.loads(path.read_text(encoding="utf-8-sig")))


def _with_candidate_commands(result, goal_id, local):
    flag = "--confirm-local" if local else "--confirm-public"
    result["formalization_commands"] = [
        f"research-multiplier goal formalize {goal_id} {result['record']['sha256']} {index} {flag}"
        for index in range(1, len(result["proposals"]) + 1)
    ]
    return result


def _execute(store, args):
    action = args.goal_action
    if action == "draft":
        return store.query("human", "SELECT * FROM rm.create_research_goal(%s,%s)",
                           _document(store, args.file), args.revision_of)[0]
    if action == "show":
        return _show(store, args.goal_id)
    if action == "confirm":
        return store.query("human", "SELECT rm.confirm_research_goal(%s,%s,%s,%s) AS id",
                           args.goal_id, args.checksum, args.actor, args.reason)[0]
    if action == "propose":
        return store.query("human", "SELECT * FROM rm.propose_research_conjecture(%s,%s)",
                           args.goal_id, _document(store, args.file))[0]
    if action == "suggest":
        from research_multiplier.workflows.goal_suggestions import suggest

        return suggest(store, args.goal_id, confirm_local=args.confirm_local)
    if action == "discussion":
        from research_multiplier.workflows.goal_suggestions import latest_discussion

        result = latest_discussion(store, args.goal_id)
        parameters = result["model"].get("parameters", {})
        return _with_candidate_commands(result, args.goal_id, bool(parameters.get("private_model")))
    if action == "discuss":
        from research_multiplier.workflows.goal_suggestions import discuss

        with args.file.open("rb") as handle:
            raw = handle.read(18_001)
        if len(raw) > 18_000:
            raise ValueError("The discussion input is limited to 18 KiB.")
        result = discuss(store, args.goal_id, json.loads(raw.decode("utf-8-sig")), confirm_local=args.confirm_local)
        return _with_candidate_commands(result, args.goal_id, args.confirm_local)
    if action == "formalize":
        from research_multiplier.workflows.goal_suggestions import formalize_discussion

        return formalize_discussion(store, args.goal_id, args.discussion_sha256, args.candidate_index,
                                    confirm_local=args.confirm_local)
    if action == "select":
        selection = store.query("human", "SELECT * FROM rm.select_research_conjecture(%s,%s,%s,%s)",
                                args.conjecture_id, args.checksum, args.actor, args.reason)[0]
        frozen = store.query("human", "SELECT contract_sha256 FROM rm.claims WHERE id = %s",
                             selection["claim_id"])[0]
        return {**selection, **frozen}
    if action == "condition":
        return store.query("human", "SELECT * FROM rm.request_research_condition(%s,%s)",
                           args.goal_id, _document(store, args.file))[0]
    if action == "reopen-condition":
        return store.query("human", "SELECT rm.reopen_research_condition(%s,%s,%s) AS id",
                           args.request_id, args.actor, args.reason)[0]
    if action == "record-derivation":
        return store.query("verifier", "SELECT * FROM rm.record_research_derivation(%s,%s)",
                           args.request_id, args.evidence_id)[0]
    if action == "record-local-derivation":
        return store.query("verifier", "SELECT * FROM rm.record_route_condition_local_lemma(%s,%s)",
                           args.request_id, args.lemma_id)[0]
    return store.query("human", "SELECT * FROM rm.decide_research_condition(%s,%s,%s,%s,%s,%s)",
                       args.request_id, args.action, args.actor, args.reason, args.version, args.evidence)[0]


def run(args):
    with Store() as store:
        result = _execute(store, args)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=_encode))
    return 0
