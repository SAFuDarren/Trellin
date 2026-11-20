# SPDX-License-Identifier: Apache-2.0
"""Check a proposed package version against the latest fetched main commit."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

PACKAGE_FILE = "pyproject.toml"
COMPAT_MARKER = "tests/compat_validation/PACKAGE_VERSION"
VERSION_PATTERN = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True)
    if result.returncode:
        raise SystemExit(result.stderr.strip() or f"git {args[0]} failed.")
    return result.stdout


def commit_at(ref: str) -> str:
    try:
        return git("rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}").strip()
    except SystemExit as exc:
        extra = " Fetch origin main before checking." if ref == "origin/main" else ""
        raise SystemExit(f"Could not resolve {ref} to a Git commit.{extra}") from exc


def package_version(commit: str) -> tuple[int, int, int]:
    content = git("show", f"{commit}:{PACKAGE_FILE}")
    # Read the static [project] version used by this repository on Python 3.10.
    project = re.search(r"(?m)^[ \t]*\[project\][ \t]*(?:#[^\n]*)?$", content)
    if project:
        section = re.split(r"(?m)^[ \t]*\[", content[project.end():], maxsplit=1)[0]
        version = re.search(
            r"""(?m)^[ \t]*version[ \t]*=[ \t]*(["'])([^"'\n]+)\1[ \t]*(?:#[^\n]*)?$""", section
        )
        match = VERSION_PATTERN.fullmatch(version[2]) if version else None
        if match:
            major, minor, patch = match.groups()
            return int(major), int(minor), int(patch)
    raise SystemExit(f"{commit[:12]}:{PACKAGE_FILE} needs a static [project] version in MAJOR.MINOR.PATCH form.")


def increment(version: tuple[int, int, int], bump: str) -> str:
    major, minor, patch = version
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main", help="Latest fetched main ref (default: origin/main).")
    parser.add_argument("--candidate", default="HEAD", help="Committed candidate ref (default: HEAD).")
    parser.add_argument("--bump", choices=("patch", "minor", "major"), default="patch")
    args = parser.parse_args()

    base = commit_at(args.base)
    candidate = commit_at(args.candidate)
    ancestry = subprocess.run(["git", "merge-base", "--is-ancestor", base, candidate], capture_output=True, text=True)
    if ancestry.returncode == 1:
        raise SystemExit(f"{args.candidate} does not include {args.base}. Rebase and recheck the version.")
    if ancestry.returncode:
        raise SystemExit(ancestry.stderr.strip() or "Could not compare Git commit ancestry.")

    base_version = package_version(base)
    candidate_version = ".".join(map(str, package_version(candidate)))
    expected = increment(base_version, args.bump)
    if candidate_version != expected:
        raise SystemExit(
            f"Expected {args.bump} version {expected} from {args.base}, found {candidate_version} on {args.candidate}. "
            f"Update {PACKAGE_FILE} and {COMPAT_MARKER} together."
        )
    marker = git("show", f"{candidate}:{COMPAT_MARKER}").strip()
    if marker != candidate_version:
        raise SystemExit(f"{COMPAT_MARKER} on {args.candidate} must match package version {candidate_version}.")

    print(f"{args.candidate} uses {candidate_version}, the expected {args.bump} version from {args.base}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
