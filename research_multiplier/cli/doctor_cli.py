"""Report which parts of the local setup are ready and how to fix the rest."""

import json

from ._command import register


def add_commands(commands):
    parser = register(commands, "doctor", handler=run, roles=(), workspace=False,
                      help="Check the installation, workspace, database and Lean setup without changing anything.")
    parser.add_argument("--json", action="store_true", help="Print the checks as JSON.")
    parser.add_argument("--skip-lean", action="store_true", help="Skip the Lean toolchain checks.")


def run(args):
    from research_multiplier.workflows.doctor import FAIL, diagnose, render

    report = diagnose(skip_lean=args.skip_lean, credentials_error=getattr(args, "credentials_error", None))
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 1 if any(check["status"] == FAIL for check in report["checks"]) else 0
