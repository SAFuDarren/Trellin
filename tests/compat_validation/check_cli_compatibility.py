"""Compare command contracts and archived verifier sources against an earlier release."""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

_INSPECT = '''
import argparse
import json
from research_multiplier.cli import build_parser

def scan(parser, path, output):
    command = parser.get_default("_command")
    if command:
        roles = command.roles
        roles = roles.__qualname__ if callable(roles) else roles
        outputs = [(item.field, item.category, item.external) for item in command.outputs]
        controls = [roles, command.workspace, outputs]
    else:
        controls = None
    entries = {}
    for argument in parser._actions:
        if isinstance(argument, argparse._SubParsersAction):
            for name, child in argument.choices.items():
                scan(child, (*path, name), output)
        else:
            key = argument.option_strings[0] if argument.option_strings else argument.dest
            choices = sorted(map(str, argument.choices)) if argument.choices is not None else None
            entries[key] = {
                "dest": argument.dest, "flags": argument.option_strings, "required": argument.required,
                "nargs": str(argument.nargs), "type": argument.type.__name__ if argument.type else None,
                "choices": choices, "default": argument.default, "action": type(argument).__name__,
            }
    output[" ".join(path)] = [controls, entries]

commands = {}
scan(build_parser(), (), commands)
print(json.dumps(commands))
'''


def commands(installation, directory):
    environment = {**os.environ, "PYTHONPATH": str(installation), "PYTHONDONTWRITEBYTECODE": "1"}
    output = subprocess.check_output([sys.executable, "-c", _INSPECT], cwd=directory, env=environment)
    return json.loads(output)


def snapshot(installation, directory):
    package = installation / "research_multiplier"
    pinned = {}
    for file in package.rglob("*.py"):
        relative = file.relative_to(package)
        if "_pinned" in relative.parts:
            pinned[relative.as_posix()] = sha256(file.read_bytes()).hexdigest()
    return {"format_version": 1, "commands": commands(installation, directory), "pinned": pinned}


def installation_directory(parser, path):
    directory = path.resolve()
    if not (directory / "research_multiplier" / "__init__.py").is_file():
        parser.error(f"Select an installation directory containing Research Multiplier: {directory}")
    return directory


def load_baseline(parser, path):
    try:
        baseline = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        parser.error(f"Could not read the CLI compatibility baseline: {error}")
    if (not isinstance(baseline, dict) or baseline.get("format_version") != 1
            or not isinstance(baseline.get("commands"), dict) or not baseline["commands"]
            or not isinstance(baseline.get("pinned"), dict)):
        parser.error(f"Invalid CLI compatibility baseline: {path}")
    return baseline


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-baseline", type=Path, metavar="OUTPUT",
                        help="Save the inspected previous installation as a JSON baseline.")
    parser.add_argument("previous", type=Path, help="Previous installation directory or saved JSON baseline.")
    parser.add_argument("current", type=Path, nargs="?", help="Current installation directory.")
    args = parser.parse_args()
    if args.write_baseline is not None and args.current is not None:
        parser.error("Supply one installation when writing a baseline.")
    if args.write_baseline is None and args.current is None:
        parser.error("Supply the current installation directory when comparing releases.")
    with TemporaryDirectory(prefix="rm-cli-contract-") as directory:
        if args.write_baseline is not None:
            baseline = snapshot(installation_directory(parser, args.previous), directory)
            args.write_baseline.write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(f"Wrote CLI compatibility baseline to {args.write_baseline}.")
            return 0
        previous = (load_baseline(parser, args.previous) if args.previous.is_file()
                    else snapshot(installation_directory(parser, args.previous), directory))
        current = snapshot(installation_directory(parser, args.current), directory)
    existing, updated = previous["commands"], current["commands"]
    problems = []
    for name, (controls, flags) in existing.items():
        if name not in updated:
            problems.append(f"Removed command: {name}")
            continue
        new_controls, new_flags = updated[name]
        if controls != new_controls:
            problems.append(f"Changed command policy: {name}")
        problems.extend(f"Changed existing argument: {name} {key}" for key, value in flags.items()
                        if new_flags.get(key) != value)
        problems.extend(f"Added mandatory argument: {name} {key}" for key in new_flags.keys() - flags.keys()
                        if new_flags[key]["required"] or not new_flags[key]["flags"])
    pinned = previous["pinned"]
    for relative, digest in pinned.items():
        if current["pinned"].get(relative) != digest:
            problems.append(f"Changed archived implementation: {relative}")
    if problems:
        print("\n".join(sorted(problems)), file=sys.stderr)
        return 1
    print(f"{len(existing)} original command contracts and {len(pinned)} archived Python files preserved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
