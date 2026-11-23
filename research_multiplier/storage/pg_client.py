"""Find PostgreSQL client tools that can work with the project's server (#321).

`pg_dump` refuses to dump a server with a newer major version, and `pg_restore` cannot read a dump written by a newer
`pg_dump`. Machines often have several clients installed, and the first one on PATH is not necessarily the right one;
an app started from Finder may not have the package manager's directories on PATH at all. So the tools are chosen by
major version: the server's own version first, then the nearest newer one, searching PATH and then the usual install
locations. An explicit `--pg-bin` directory is always respected.
"""

import os
from pathlib import Path
import re
import shutil
import subprocess

_VERSION = re.compile(r"\)\s*(\d+)")


def server_major(version_num):
    """Turn PostgreSQL's numeric server version (for example 180004) into its major version."""
    return int(version_num) // 10000 if version_num else None


def client_major(executable):
    """Return the major version that a client tool reports, or None when it cannot be read."""
    try:
        completed = subprocess.run([str(executable), "--version"], capture_output=True, text=True, timeout=10,
                                   check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = _VERSION.search(completed.stdout) if completed.returncode == 0 else None
    return int(match.group(1)) if match else None


def install_directories():
    """Common PostgreSQL client locations that may be missing from PATH, newest first within each family."""
    def versions(base, pattern):
        return sorted(base.glob(pattern), key=lambda path: _number(path), reverse=True)

    home = Path.home()
    found = []
    for prefix in (Path("/opt/homebrew/opt"), Path("/usr/local/opt")):
        found += versions(prefix, "postgresql@*/bin") + [prefix / "postgresql" / "bin", prefix / "libpq" / "bin"]
    for app in (Path("/Applications/Postgres.app"), home / "Applications" / "Postgres.app"):
        found += versions(app / "Contents" / "Versions", "*/bin")
    found += versions(Path("/usr/lib/postgresql"), "*/bin") + versions(Path("/usr"), "pgsql-*/bin")
    for variable in ("ProgramFiles", "ProgramW6432"):
        if os.environ.get(variable):
            found += versions(Path(os.environ[variable]) / "PostgreSQL", "*/bin")
    return found


def _number(path):
    """The version in a directory such as postgresql@18/bin, Versions/16/bin or pgsql-15/bin."""
    numbers = re.findall(r"\d+", str(path))
    return int(numbers[-1]) if numbers else 0


def candidates(tool):
    """Every distinct executable named `tool`, in PATH order and then the install locations."""
    name = tool + (".exe" if os.name == "nt" else "")
    directories = [Path(entry) for entry in os.environ.get("PATH", "").split(os.pathsep) if entry]
    seen, found = set(), []
    for directory in directories + install_directories():
        executable = directory / name
        try:
            if not executable.is_file() or not os.access(executable, os.X_OK):
                continue
            key = executable.resolve()
        except OSError:
            continue
        if key not in seen:
            seen.add(key)
            found.append(executable)
    return found


def resolve(tool, server, pg_bin=None):
    """Choose the `tool` executable for a server of major version `server` (None when unknown)."""
    if pg_bin is not None:
        executable = shutil.which(str(Path(pg_bin) / tool))
        if not executable:
            raise ValueError(f"Install a PostgreSQL client with {tool} or provide --pg-bin.")
        return executable
    options = [(path, client_major(path)) for path in candidates(tool)]
    if not options:
        raise ValueError(f"Install a PostgreSQL client with {tool} or provide --pg-bin.")
    if server is None:
        return str(options[0][0])
    usable = [(major, order, path) for order, (path, major) in enumerate(options)
              if major is not None and major >= server]
    if not usable:
        majors = sorted({major for _, major in options if major is not None})
        found = ", ".join(f"PostgreSQL {major}" for major in majors) or "a client of unknown version"
        raise ValueError(f"The installed {tool} ({found}) is older than the PostgreSQL {server} server. "
                         f"Install the PostgreSQL {server} client tools, or pass --pg-bin.")
    # The server's own major version first, then the nearest newer one; PATH order breaks ties.
    _, _, path = min(usable, key=lambda option: (option[0] != server, option[0], option[1]))
    return str(path)
