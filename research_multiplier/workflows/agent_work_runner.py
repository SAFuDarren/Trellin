"""Validate a scoped Agent's output and replay code patches against pinned inputs."""

import difflib
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

from research_multiplier.workflows.agent_work_git import (
    isolated_git_environment,
    validate_code_path,
    validate_code_revision,
)


_MAX_FILES = 12
_MAX_TEXT_BYTES = 100 * 1024
_MAX_FILE_LINES = 10_000
_MAX_PATCH_BYTES = 256 * 1024


def _text(value, limit, *, empty=False):
    if not isinstance(value, str) or len(value) > limit or "\x00" in value or (not empty and not value.strip()):
        raise ValueError("The work result must contain bounded UTF-8 text.")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError:
        raise ValueError("The work result must contain bounded UTF-8 text.") from None
    if size > limit:
        raise ValueError("The work result must contain bounded UTF-8 text.")
    return value, size


def _files(inputs, changes):
    if not isinstance(inputs, dict) or set(inputs) != {"revision", "files"}:
        raise ValueError("Implementation requires an exact code input snapshot.")
    validate_code_revision(inputs["revision"])
    originals = inputs["files"]
    if not isinstance(originals, dict) or not 1 <= len(originals) <= _MAX_FILES:
        raise ValueError("Implementation requires a bounded set of pinned files.")
    if not isinstance(changes, list) or not 1 <= len(changes) <= len(originals):
        raise ValueError("Implementation must replace at least one pinned file.")
    remaining = _MAX_TEXT_BYTES
    for name, content in originals.items():
        validate_code_path(name)
        _, size = _text(content, remaining, empty=True)
        if content.count("\n") > _MAX_FILE_LINES:
            raise ValueError("The code input exceeds the patch line limit.")
        remaining -= size
    replacements = {}
    remaining = _MAX_TEXT_BYTES
    for change in changes:
        if not isinstance(change, dict) or set(change) != {"path", "content"}:
            raise ValueError("Each change must name one pinned file and its replacement text.")
        name = change["path"]
        validate_code_path(name)
        if name not in originals or name in replacements:
            raise ValueError("Each changed path must be an approved pinned file listed once.")
        content, size = _text(change["content"], remaining, empty=True)
        if content == originals[name]:
            raise ValueError("Each replacement must change its pinned file.")
        if content.count("\n") > _MAX_FILE_LINES:
            raise ValueError("The replacement exceeds the patch line limit.")
        remaining -= size
        replacements[name] = content
    return originals, replacements


def _git_path(name):
    escaped = []
    for byte in name.encode("utf-8"):
        if byte in {34, 92}:
            escaped.append("\\" + chr(byte))
        elif 32 <= byte < 127:
            escaped.append(chr(byte))
        else:
            escaped.append(f"\\{byte:03o}")
    return '"' + "".join(escaped) + '"'


def _lines(text):
    lines = text.split("\n")
    output = [line + "\n" for line in lines[:-1]]
    if lines[-1]:
        output.append(lines[-1])
    return output


def _patch(originals, replacements):
    patch = []
    for name, content in sorted(replacements.items()):
        before, after = _git_path("a/" + name), _git_path("b/" + name)
        patch.append(f"diff --git {before} {after}\n")
        for line in difflib.unified_diff(_lines(originals[name]), _lines(content), fromfile=before, tofile=after):
            patch.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(patch)


def _check_patch(originals, replacements, patch):
    git = shutil.which("git")
    if git is None:
        raise ValueError("Git is required to verify a proposed patch.")
    encoded = patch.encode("utf-8")
    if len(encoded) > _MAX_PATCH_BYTES:
        raise ValueError("The proposed patch exceeds the delivery size limit.")
    try:
        with TemporaryDirectory(prefix="rm-agent-patch-") as directory:
            root = Path(directory).resolve()
            materialized = {}
            for name in replacements:
                parts = validate_code_path(name).parts
                if os.name == "nt" and any(
                    part.endswith((" ", ".")) or Path(part).is_reserved() or any(char in part for char in ':<>"|?*')
                    for part in parts
                ):
                    raise ValueError("The proposed patch contains a file path that is unsupported on this machine.")
                target = root.joinpath(*parts)
                if not target.resolve().is_relative_to(root):
                    raise ValueError("The proposed patch contains a file path outside its verification directory.")
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as stream:
                    stream.write(originals[name].encode("utf-8"))
                materialized[name] = target
            subprocess.run(
                [git, "apply", "--whitespace=nowarn", "-"],
                cwd=root,
                env={**isolated_git_environment(), "GIT_CEILING_DIRECTORIES": str(root.parent)},
                input=encoded,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=True,
            )
            if any(path.read_bytes() != replacements[name].encode("utf-8") for name, path in materialized.items()):
                raise ValueError("The proposed patch did not replay to the expected text.")
    except (OSError, RuntimeError, subprocess.SubprocessError):
        raise ValueError("The proposed patch could not be verified against its pinned inputs.") from None


def build_delivery(mode, inputs, result):
    """Return a bounded delivery for a typed model result.

    Implementation replaces selected UTF-8 files in a transient verification directory.
    The returned Git patch and replay check describe the pinned input snapshot.
    """
    if mode == "implement":
        field = "changes"
    elif mode in ("review", "draft"):
        field = "body"
    else:
        raise ValueError("The work result mode is unavailable.")
    if not isinstance(result, dict) or set(result) != {"summary", field}:
        raise ValueError("The work result did not match its expected fields.")
    summary, _ = _text(result["summary"], 400)
    if not summary.isprintable():
        raise ValueError("The work result summary must fit on one line.")
    if mode == "implement":
        originals, replacements = _files(inputs, result[field])
        body = _patch(originals, replacements)
        _check_patch(originals, replacements, body)
        return {"kind": "patch", "summary": summary.strip(), "body": body, "checks": ["patch_applies"]}
    body, _ = _text(result[field], 16_000)
    return {"kind": mode, "summary": summary.strip(), "body": body, "checks": []}
