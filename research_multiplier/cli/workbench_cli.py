"""Launch the local proof workbench for the selected project."""

from uuid import UUID

from research_multiplier.storage import paths

from ._command import register


def add_commands(commands):
    parser = register(commands, "workbench", handler=run, help="Open the local proof workbench.")
    parser.add_argument("claim_id", type=UUID, nargs="?", help="Start at this frozen claim.")
    parser.add_argument("--port", type=int, default=0, help="Loopback port. The default chooses a free port.")
    parser.add_argument("--no-browser", action="store_true", help="Print the session link without opening it.")


def run(args):
    from research_multiplier.workbench.server import serve

    serve(paths.require_workspace(), claim_id=args.claim_id, port=args.port, open_browser=not args.no_browser)
    return 0
