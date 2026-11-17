"""Keep a project's database logins in a private file outside the workspace.

The file is written by `setup` and loaded when a command selects that workspace.
Connection settings already present in the environment take precedence: when any
of them is set, the file is not read, so settings are never mixed between sources
and subprocesses that receive a filtered environment keep only what they were given.
"""

import os
from pathlib import Path
import stat
from tempfile import NamedTemporaryFile


KEYS = ("RM_HUMAN_DSN", "RM_SCHEDULER_DSN", "RM_AGENT_DSN", "RM_VERIFIER_DSN", "RM_MIGRATION_DSN")


def directory():
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base).expanduser() / "research-multiplier" / "workspaces"


def location(workspace_id):
    return directory() / f"{workspace_id}.env"


def read(workspace_id):
    path = location(workspace_id)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"The project login file must be a regular file: {path}")
    if os.name != "nt" and (info.st_mode & 0o077 or info.st_uid != os.getuid()):
        raise ValueError(f"Only your user may read the project login file. Run: chmod 600 {path}")
    values = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key not in KEYS or len(value) < 2 or value[0] != "'" or value[-1] != "'":
            raise ValueError(f"Unsupported line {number} in the project login file {path}.")
        values[key] = value[1:-1]
    return values


def write(workspace_id, values):
    if set(values) - set(KEYS) or any("'" in value or "\n" in value for value in values.values()):
        raise ValueError("Unsupported project login settings.")
    folder = directory()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    text = "# Research Multiplier database logins for one workspace. Keep this file private.\n"
    text += "".join(f"{key}='{values[key]}'\n" for key in KEYS if key in values)
    with NamedTemporaryFile("w", dir=folder, prefix=".rm-logins-", delete=False, encoding="utf-8") as stream:
        temporary = Path(stream.name)
        os.chmod(temporary, 0o600)
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, location(workspace_id))
    return location(workspace_id)


def configured_in_environment():
    return any(os.environ.get(key) for key in KEYS)


def load(workspace):
    """Fill the process environment from the selected workspace's login file."""
    if workspace is None or configured_in_environment():
        return None
    from research_multiplier.storage import paths

    identifier = paths.workspace_id(workspace)
    if identifier is None:
        return None
    values = read(identifier)
    if values:
        os.environ.update(values)
        return location(identifier)
    return None
