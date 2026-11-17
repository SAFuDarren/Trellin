"""Declare command routing, workspace rules, and required database roles."""

from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path

from research_multiplier.storage import paths


HUMAN = ("human",)
VERIFIER = ("verifier",)
VERIFY = ("scheduler", "verifier")
PROVE = ("scheduler", "agent", "verifier")
ALL = (*HUMAN, *PROVE)


@dataclass(frozen=True)
class Output:
    field: str
    category: str | None = None
    external: bool = False


@dataclass(frozen=True)
class Command:
    roles: tuple[str, ...] | Callable
    workspace: bool
    outputs: tuple[Output, ...]

    def required_roles(self, args):
        return self.roles(args) if callable(self.roles) else self.roles


def register(parsers, name, *, help, handler=None, roles=HUMAN, workspace=True, outputs=()):
    parser = parsers.add_parser(name, help=help)
    parser.set_defaults(_command=Command(roles, workspace, outputs))
    if handler is not None:
        parser.set_defaults(_handler=handler)
    return parser


def required_roles(args):
    return args._command.required_roles(args)


def prepare_paths(args):
    command = args._command
    for output in command.outputs:
        value = getattr(args, output.field, None)
        if value is not None:
            checked = (paths.external_destination(value, output.category) if output.external
                       else paths.category_path(value, output.category))
            setattr(args, output.field, checked)
    if not command.workspace:
        return
    if hasattr(args, "mathlib_project") and args.mathlib_project is None:
        configured = os.environ.get("RM_LEAN_MATHLIB_PROJECT")
        if configured:
            paths.validate_project_read(configured)
    for field, location in vars(args).items():
        if isinstance(location, Path):
            setattr(args, field, paths.validate_project_read(location))


def preflight(args):
    roles = required_roles(args)
    if roles:
        from research_multiplier.storage.postgres import Store

        with Store() as store:
            store.verify_runtime_roles(roles=roles)
