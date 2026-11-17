"""Read-only checks of the local installation, workspace, database and Lean setup."""

from dataclasses import asdict, dataclass
from importlib.metadata import PackageNotFoundError, version
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

from research_multiplier.storage import credentials, paths


OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"
ROLES = ("human", "scheduler", "agent", "verifier")
LEAN_TOOLCHAIN = "leanprover/lean4:v4.19.0"
LEAN_VERSION_MARKERS = ("version 4.19.0,", "commit 6caaee842e94,")
MATHLIB_SAMPLE = Path("Mathlib") / "Data" / "Real" / "Basic.olean"
POSTGRES_MODULES = ("psycopg", "alembic", "jsonschema")
CAPABILITIES = {
    "offline": ("Offline briefs and replay", ("python", "workspace")),
    "database": ("Persistent research (PostgreSQL)",
                 ("python", "workspace", "postgres-extra", "connections", "schema", "binding")),
    "lean": ("Lean proof checks", ("python", "workspace", "lean-platform", "lean-project", "lean-toolchain")),
}


@dataclass
class Check:
    id: str
    group: str
    title: str
    status: str
    detail: str = ""
    fix: str = ""


def _run(command, *, cwd=None, timeout=20):
    try:
        completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed


def elan_default(elan):
    """Return elan's default toolchain, which is active outside any Lean project."""
    with TemporaryDirectory(prefix="rm-doctor-") as directory:
        shown = _run([elan, "show"], cwd=directory, timeout=15)
    return next((line.split()[0] for line in (shown.stdout.splitlines() if shown else ())
                 if line.rstrip().endswith("(default)")), None)


def _major(text):
    match = re.search(r"(\d+)(?:\.\d+)?", text or "")
    return int(match.group(1)) if match else None


class Doctor:
    def __init__(self):
        self.checks = []
        self.workspace = None
        self.store = None
        self.server_major = None
        self.credentials_error = None

    def add(self, id, group, title, status, detail="", fix=""):
        self.checks.append(Check(id, group, title, status, detail, fix))

    def status(self, id):
        return next((check.status for check in self.checks if check.id == id), SKIP)

    # Basics

    def python(self):
        try:
            installed = version("research-multiplier")
        except PackageNotFoundError:
            installed = "source checkout"
        current = platform.python_version()
        if sys.version_info < (3, 10):
            self.add("python", "Basics", "Python", FAIL, f"Python {current}", "Install Python 3.10 or later.")
        else:
            self.add("python", "Basics", "Python", OK, f"Python {current}, Research Multiplier {installed}")

    def workspace_check(self):
        fix = "research-multiplier research init-workspace PATH, then pass --workspace PATH or run inside it"
        try:
            self.workspace = paths.active_workspace()
            if self.workspace is not None:
                paths._check_layout(self.workspace)
        except ValueError as error:
            self.workspace = None
            self.add("workspace", "Basics", "Research workspace", FAIL, str(error), fix)
            return
        if self.workspace is None:
            self.add("workspace", "Basics", "Research workspace", WARN, "No workspace selected.", fix)
            return
        missing = [name for name in paths.WORKSPACE_DIRECTORIES if not (self.workspace / name).is_dir()]
        if missing:
            self.add("workspace", "Basics", "Research workspace", WARN,
                     f"{self.workspace} is missing {', '.join(missing)}.",
                     f"research-multiplier research init-workspace {self.workspace}")
        else:
            self.add("workspace", "Basics", "Research workspace", OK, str(self.workspace))

    # Database

    def postgres_extra(self):
        missing = []
        for module in POSTGRES_MODULES:
            try:
                __import__(module)
            except ImportError:
                missing.append(module)
        if missing:
            self.add("postgres-extra", "Database", "PostgreSQL Python packages", FAIL,
                     f"Missing {', '.join(missing)}.", "pip install 'research-multiplier[postgres]'")
        else:
            self.add("postgres-extra", "Database", "PostgreSQL Python packages", OK, "psycopg, alembic, jsonschema")

    def login_file(self):
        group, title = "Database", "Project login file"
        if self.credentials_error:
            self.add("login-file", group, title, FAIL, self.credentials_error)
            return
        identifier = paths.workspace_id(self.workspace) if self.workspace is not None else None
        try:
            saved = credentials.read(identifier) if identifier is not None else None
        except ValueError as error:
            self.add("login-file", group, title, FAIL, str(error))
            return
        if saved and all(os.environ.get(key) == value for key, value in saved.items()):
            self.add("login-file", group, title, OK, f"Loaded from {credentials.location(identifier)}")
        elif saved and credentials.configured_in_environment():
            self.add("login-file", group, title, WARN,
                     "RM_*_DSN settings in the environment override the saved project logins.",
                     "Unset the RM_*_DSN variables to use this project's saved logins.")
        elif credentials.configured_in_environment():
            self.add("login-file", group, title, OK, "Using RM_*_DSN settings from the environment")
        else:
            self.add("login-file", group, title, SKIP, "No saved project logins.")

    def connections(self):
        group, title = "Database", "Role connections"
        if self.status("postgres-extra") != OK:
            self.add("connections", group, title, SKIP, "Needs the PostgreSQL Python packages.")
            return
        missing = [f"RM_{role.upper()}_DSN" for role in ROLES if not os.environ.get(f"RM_{role.upper()}_DSN")]
        if missing:
            self.add("connections", group, title, FAIL, f"Not set: {', '.join(missing)}.",
                     "research-multiplier setup")
            return
        from research_multiplier.storage.postgres import Store

        try:
            self.store = Store(allow_unbound=self.workspace is not None)
        except ValueError as error:
            self.add("connections", group, title, FAIL, str(error))
            return
        from research_multiplier.storage.postgres import DatabaseOperationError

        unreachable, problems = [], []
        for role in ROLES:
            try:
                self.store._call(role, lambda connection, role=role: self.store._verify_role(role, connection))
            except DatabaseOperationError as error:
                if error.state is None or error.state.startswith(("08", "28")):
                    unreachable.append(role)
                else:
                    problems.append(str(error))
            except ValueError as error:
                problems.append(str(error))
        mismatched = [problem for problem in problems if "different research" in problem]
        problems = [problem for problem in problems if problem not in mismatched]
        fixes = []
        if unreachable:
            problems.insert(0, f"Cannot sign in as {', '.join(unreachable)}.")
            fixes.append("Check that the PostgreSQL server is running and that each RM_*_DSN login exists "
                         "with the right password.")
        if problems[len(unreachable[:1]):]:
            fixes.append("Give each RM_*_DSN its own login that belongs only to its rm_<role> group.")
        if mismatched:
            problems.append("This workspace and the configured database belong to different research projects.")
            fixes.append("Point the RM_*_DSN settings at this project's own database, or select the workspace "
                         "that is bound to this database.")
        if problems:
            self.add("connections", group, title, FAIL, " ".join(problems), " ".join(fixes))
        else:
            self.add("connections", group, title, OK, "human, scheduler, agent and verifier logins verified")

    def schema(self):
        group, title = "Database", "pgvector and schema version"
        if self.status("connections") != OK:
            self.add("schema", group, title, SKIP, "Needs working role connections.")
            return
        try:
            row = self.store.query("human", """
                SELECT current_setting('server_version') AS server,
                       (SELECT extversion FROM pg_extension WHERE extname = 'vector') AS vector
            """)[0]
        except ValueError as error:
            self.add("schema", group, title, FAIL, str(error))
            return
        row = {key: value.decode("utf-8", "replace") if isinstance(value, bytes) else value
               for key, value in row.items()}
        self.server_major = _major(row["server"])
        if row["vector"] is None:
            self.add("schema", group, title, FAIL, f"PostgreSQL {row['server']} without pgvector.",
                     "Ask the database administrator to run CREATE EXTENSION vector; in this database.")
            return
        from research_multiplier.storage.migrate import migration_head

        head = migration_head()
        detail = f"PostgreSQL {row['server']}, pgvector {row['vector']}"
        owner = os.environ.get("RM_MIGRATION_DSN")
        if not owner:
            # Only the migration owner can read the revision table.
            self.add("schema", group, title, OK, f"{detail}; set RM_MIGRATION_DSN to compare the schema with {head}")
            return
        import psycopg

        try:
            with psycopg.connect(owner, connect_timeout=5) as connection:
                current = connection.execute("SELECT version_num FROM public.rm_alembic_version").fetchone()
        except psycopg.Error:
            self.add("schema", group, title, WARN, f"{detail}; the RM_MIGRATION_DSN login cannot read the schema "
                     "revision.", "Check that RM_MIGRATION_DSN uses this database's owner login.")
            return
        revision = current[0] if current else None
        detail = f"{detail}, schema {revision}"
        if revision != head:
            self.add("schema", group, title, WARN, f"{detail}; this release expects {head}.",
                     "Back up the project, then upgrade the schema with RM_MIGRATION_DSN "
                     "(docs/research-foundation.md#set-up-the-workspace).")
        else:
            self.add("schema", group, title, OK, detail)

    def binding(self):
        group, title = "Database", "Workspace binding"
        if self.workspace is None or self.status("connections") != OK:
            self.add("binding", group, title, SKIP, "Needs a workspace and working role connections.")
            return
        try:
            bound = self.store.query("human", "SELECT * FROM rm.current_workspace_binding()")[0]
        except ValueError as error:
            self.add("binding", group, title, FAIL, str(error))
            return
        local = paths.database_id(self.workspace)
        fix = f"research-multiplier --workspace {self.workspace} research bind-workspace"
        if local is None and bound["workspace_id"] is None:
            self.add("binding", group, title, FAIL, "The workspace and database are not bound yet.", fix)
        elif local != bound["database_id"] or bound["workspace_id"] != paths.workspace_id(self.workspace):
            self.add("binding", group, title, FAIL,
                     "This workspace and the configured database belong to different projects.",
                     "Point the RM_*_DSN settings at this project's own database.")
        else:
            self.add("binding", group, title, OK, f"database {local}")

    def client_tools(self):
        group, title = "Database", "Backup tools (pg_dump, pg_restore)"
        found = {tool: shutil.which(tool) for tool in ("pg_dump", "pg_restore")}
        missing = [tool for tool, location in found.items() if location is None]
        if missing:
            self.add("pg-tools", group, title, WARN, f"Not on PATH: {', '.join(missing)}.",
                     "Install the PostgreSQL client tools, or pass --pg-bin to backup and restore commands.")
            return
        completed = _run([found["pg_dump"], "--version"], timeout=10)
        reported = re.search(r"\)\s*(\d+)", completed.stdout) if completed and completed.returncode == 0 else None
        client = int(reported.group(1)) if reported else None
        detail = f"{found['pg_dump']} (major version {client})"
        if self.server_major and client and client < self.server_major:
            self.add("pg-tools", group, title, WARN,
                     f"{detail} is older than the PostgreSQL {self.server_major} server; backups will fail.",
                     f"Put the PostgreSQL {self.server_major} client tools first on PATH, or pass --pg-bin.")
        else:
            self.add("pg-tools", group, title, OK, detail)
        if self.status("connections") != OK:
            self.add("migration-dsn", group, "Migration login", SKIP, "Needs working role connections.")
        elif not os.environ.get("RM_MIGRATION_DSN"):
            self.add("migration-dsn", group, "Migration login", WARN,
                     "RM_MIGRATION_DSN is not set. Schema upgrades, backups and restores need it.",
                     "Set RM_MIGRATION_DSN to the database owner's connection string when you need those.")
        else:
            self.add("migration-dsn", group, "Migration login", OK, "RM_MIGRATION_DSN is set")

    # Lean

    def lean_platform(self):
        system = platform.system()
        if system == "Darwin" or (system == "Linux" and platform.libc_ver()[0] == "glibc"):
            self.add("lean-platform", "Lean", "Platform", OK, f"{system} {platform.machine()}")
        else:
            self.add("lean-platform", "Lean", "Platform", FAIL,
                     f"{system} {platform.machine()} is not supported for native Lean checks.",
                     "Use macOS or a glibc-based Linux for Lean verification.")

    def lean_project(self):
        group, title = "Lean", "Pinned Lean project"
        if self.workspace is None:
            self.add("lean-project", group, title, SKIP, "Needs a workspace.")
            return
        from research_multiplier.proof.lean_runner import digest
        from research_multiplier.proof.packaged_lean import SETUP_SOURCES

        project = self.workspace / "lean-project"
        fix = f"research-multiplier --workspace {self.workspace} lean prepare"
        missing = [name for name in SETUP_SOURCES if not (project / name).is_file()]
        if missing:
            self.add("lean-project", group, title, FAIL, f"{project} has no {', '.join(missing)}.", fix)
            return
        changed = [name for name, checksum in SETUP_SOURCES.items()
                   if digest((project / name).read_bytes()) != checksum]
        if changed:
            self.add("lean-project", group, title, FAIL,
                     f"{', '.join(changed)} differ from the pinned setup.",
                     "Restore the pinned files, or prepare a fresh lean-project/ directory.")
        else:
            self.add("lean-project", group, title, OK, str(project))

    def lean_toolchain(self):
        group, title = "Lean", "Lean 4.19.0"
        elan = shutil.which("elan")
        lean = shutil.which("lean")
        install = f"Install elan (https://lean-lang.org), then run: elan toolchain install {LEAN_TOOLCHAIN}"
        if elan is None and lean is None:
            self.add("lean-toolchain", group, title, FAIL, "Neither elan nor lean is on PATH.", install)
            return
        default = None
        if elan is not None:
            listing = _run([elan, "toolchain", "list"], timeout=15)
            lines = listing.stdout.splitlines() if listing and listing.returncode == 0 else []
            installed = [line.split()[0] for line in lines if line.strip()]
            default = elan_default(elan)
            if not any(name in (LEAN_TOOLCHAIN, LEAN_TOOLCHAIN.replace(":", "---").replace("/", "--"))
                       or name.endswith("v4.19.0") for name in installed):
                self.add("lean-toolchain", group, title, FAIL, "elan is installed but Lean 4.19.0 is not.",
                         f"elan toolchain install {LEAN_TOOLCHAIN}")
                return
            if lean is None:
                self.add("lean-toolchain", group, title, FAIL, "elan has Lean 4.19.0, but lean is not on PATH.",
                         "Add elan's bin directory (usually ~/.elan/bin) to PATH.")
                return
            prefix = _run([lean, f"+{LEAN_TOOLCHAIN}", "--print-prefix"], timeout=20)
            executable = (Path(prefix.stdout.strip()) / "bin" / "lean"
                          if prefix and prefix.returncode == 0 and prefix.stdout.strip() else None)
        else:
            executable = Path(lean)
        reported = _run([str(executable), "--version"], timeout=20) if executable else None
        text = reported.stdout.strip() if reported and reported.returncode == 0 else ""
        if not all(marker in text for marker in LEAN_VERSION_MARKERS):
            self.add("lean-toolchain", group, title, FAIL, f"Found: {text or 'no usable lean'}.", install)
            return
        if platform.system() == "Darwin" and f"{platform.machine()}-apple-darwin" not in text:
            self.add("lean-toolchain", group, title, FAIL, "Lean is not built for this Mac's architecture.",
                     f"Reinstall {LEAN_TOOLCHAIN} for {platform.machine()}.")
            return
        if elan is not None and not (default or "").endswith("v4.19.0"):
            current = f"elan's default is {default}" if default else "elan has no default toolchain"
            self.add("lean-toolchain", group, title, WARN,
                     f"Lean 4.19.0 is installed, but {current}. Lean Core checks without --lean use the default.",
                     f"elan default {LEAN_TOOLCHAIN}")
            return
        self.add("lean-toolchain", group, title, OK, str(executable))

    def mathlib(self):
        group, title = "Lean", "Mathlib build (optional)"
        configured = os.environ.get("RM_LEAN_MATHLIB_PROJECT")
        if configured:
            project = Path(configured).expanduser()
        elif self.status("lean-project") == OK:
            project = self.workspace / "lean-project"
        else:
            self.add("mathlib", group, title, SKIP, "Needs the pinned Lean project.")
            return
        built = project / ".lake" / "packages" / "mathlib" / ".lake" / "build" / "lib" / "lean"
        if (built / MATHLIB_SAMPLE).is_file():
            self.add("mathlib", group, title, OK, str(project))
        else:
            self.add("mathlib", group, title, WARN,
                     f"No compiled Mathlib in {project}. Lean Core checks work without it.",
                     f"cd {project} && lake exe cache get")

    # Models

    def models(self):
        if os.environ.get("RESEARCH_MULTIPLIER_API_KEY"):
            self.add("models", "Models", "Remote model key (optional)", OK, "RESEARCH_MULTIPLIER_API_KEY is set")
        else:
            self.add("models", "Models", "Remote model key (optional)", SKIP,
                     "Not set. Verification and replay need no model.")

    def run(self, *, skip_lean=False):
        try:
            self.python()
            self.workspace_check()
            self.postgres_extra()
            self.login_file()
            self.connections()
            self.schema()
            self.binding()
            self.client_tools()
        finally:
            if self.store is not None:
                self.store.__exit__(None, None, None)
        if skip_lean:
            for id, title in (("lean-platform", "Platform"), ("lean-project", "Pinned Lean project"),
                              ("lean-toolchain", "Lean 4.19.0")):
                self.add(id, "Lean", title, SKIP, "Skipped with --skip-lean.")
        else:
            self.lean_platform()
            self.lean_project()
            self.lean_toolchain()
            self.mathlib()
        self.models()
        return self.report()

    def report(self):
        capabilities = {
            key: {"title": title, "ready": all(self.status(id) in (OK, WARN) for id in required)}
            for key, (title, required) in CAPABILITIES.items()
        }
        return {"capabilities": capabilities, "checks": [asdict(check) for check in self.checks]}


def diagnose(*, skip_lean=False, credentials_error=None):
    check = Doctor()
    check.credentials_error = credentials_error
    return check.run(skip_lean=skip_lean)


_MARKS = {OK: "ok  ", WARN: "warn", FAIL: "FAIL", SKIP: "skip"}


def render(report):
    lines, group = [], None
    for check in report["checks"]:
        if check["group"] != group:
            group = check["group"]
            lines.append(f"\n{group}")
        lines.append(f"  [{_MARKS[check['status']]}] {check['title']}: {check['detail']}".rstrip(": "))
        if check["fix"] and check["status"] in (WARN, FAIL):
            lines.append(f"         Fix: {check['fix']}")
    lines.append("\nReady to use")
    for capability in report["capabilities"].values():
        lines.append(f"  {'yes' if capability['ready'] else 'no '}  {capability['title']}")
    return "\n".join(lines).lstrip("\n")
