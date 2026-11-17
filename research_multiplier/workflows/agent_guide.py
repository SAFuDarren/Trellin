"""Ship the agent guide and keep a copy in each research workspace."""

import hashlib
from importlib.metadata import PackageNotFoundError, version
from importlib.resources import files
from pathlib import Path


GUIDE = "AGENTS.md"
CLAUDE = "CLAUDE.md"
CLAUDE_TEXT = "@AGENTS.md\n"
_MARKER = "<!-- research-multiplier agent guide"


def text():
    return files("research_multiplier").joinpath("agent_guide.md").read_text(encoding="utf-8")


def _installed_version():
    try:
        return version("research-multiplier")
    except PackageNotFoundError:
        return "source"


def _render(body):
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return f"{_MARKER} {_installed_version()} sha256:{digest} -->\n{body}"


def _generated_and_unchanged(content):
    first, _, body = content.partition("\n")
    if not first.startswith(_MARKER) or "sha256:" not in first:
        return False
    recorded = first.split("sha256:", 1)[1].split()[0]
    return hashlib.sha256(body.encode("utf-8")).hexdigest() == recorded


def install(workspace):
    """Write or refresh AGENTS.md and a CLAUDE.md pointer. Never overwrite edited files."""
    root = Path(workspace)
    result = {}
    guide = root / GUIDE
    rendered = _render(text())
    if guide.is_symlink() or (guide.exists() and not guide.is_file()):
        result[GUIDE] = "kept"
    elif not guide.exists():
        guide.write_text(rendered, encoding="utf-8")
        result[GUIDE] = "written"
    else:
        current = guide.read_text(encoding="utf-8", errors="replace")
        if current == rendered:
            result[GUIDE] = "current"
        elif _generated_and_unchanged(current):
            guide.write_text(rendered, encoding="utf-8")
            result[GUIDE] = "updated"
        else:
            result[GUIDE] = "kept"
    claude = root / CLAUDE
    if not claude.exists() and not claude.is_symlink():
        claude.write_text(CLAUDE_TEXT, encoding="utf-8")
        result[CLAUDE] = "written"
    else:
        result[CLAUDE] = "kept"
    return result
