"""Print the guide for AI agents, or refresh the selected workspace's copy."""

import json

from research_multiplier.storage import paths

from ._command import register


def add_commands(commands):
    parser = register(commands, "agent-guide", handler=run, roles=(), workspace=False,
                      help="Print the guide for AI agents working in a research workspace.")
    parser.add_argument("--install", action="store_true",
                        help="Write AGENTS.md and CLAUDE.md into the selected workspace, keeping edited files.")


def run(args):
    from research_multiplier.workflows import agent_guide

    if args.install:
        root = paths.require_workspace()
        print(json.dumps({"workspace": str(root), "agent_files": agent_guide.install(root)}, indent=2))
    else:
        print(agent_guide.text(), end="")
    return 0
