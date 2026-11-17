"""Prepare a research workspace, its PostgreSQL database and Lean in one command."""

from pathlib import Path

from ._command import register


def add_commands(commands):
    parser = register(commands, "setup", handler=run, roles=(), workspace=False,
                      help="Set up a workspace, its database on an existing PostgreSQL server, and Lean.")
    parser.add_argument("path", type=Path, nargs="?",
                        help="Research workspace folder (default: the selected workspace, or ask).")
    parser.add_argument("--host", help="PostgreSQL server host or socket directory (default: libpq defaults).")
    parser.add_argument("--port", type=int, help="PostgreSQL server port.")
    parser.add_argument("--admin-user", help="PostgreSQL superuser used only during setup.")
    parser.add_argument("--yes", action="store_true", help="Run the listed installs without asking.")
    parser.add_argument("--mathlib", action="store_true", help="Also download compiled Mathlib (several GB).")
    parser.add_argument("--skip-lean", action="store_true", help="Set up only the workspace and database.")


def run(args):
    from research_multiplier.workflows.doctor import FAIL
    from research_multiplier.workflows.setup import Setup, choose_workspace

    workspace = choose_workspace(args.path)
    report = Setup(workspace, host=args.host, port=args.port, admin_user=args.admin_user, assume_yes=args.yes,
                   mathlib=args.mathlib, skip_lean=args.skip_lean).run()
    return 1 if any(check["status"] == FAIL for check in report["checks"]) else 0
