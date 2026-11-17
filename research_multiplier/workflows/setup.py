"""Prepare a research workspace on an existing PostgreSQL server and the pinned Lean toolchain.

Every step is safe to repeat. Software installs and large downloads are listed first
and run only after confirmation (or --yes). Administrator credentials are used for
this run only and are never written to disk.
"""

from getpass import getpass
from importlib import invalidate_caches
from importlib.metadata import requires
import os
from pathlib import Path
import platform
import secrets
import shutil
import subprocess
import sys
from time import monotonic, sleep
from urllib.parse import quote

from research_multiplier.storage import credentials, paths
from research_multiplier.workflows import doctor


GROUPS = ("human", "scheduler", "agent", "verifier")
MIN_SERVER = 180000
ELAN_SCRIPT = "https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh"


class SetupError(ValueError):
    pass


class Setup:
    def __init__(self, workspace, *, host=None, port=None, admin_user=None, assume_yes=False, mathlib=False,
                 skip_lean=False, interactive=None):
        self.workspace = workspace
        self.host, self.port, self.admin_user = host, port, admin_user
        self.assume_yes, self.mathlib, self.skip_lean = assume_yes, mathlib, skip_lean
        self.interactive = sys.stdin.isatty() if interactive is None else interactive
        self.declined = []

    # Shared helpers

    def say(self, text=""):
        print(text, flush=True)

    def confirm(self, question, commands, *, default_yes=True):
        self.say("  Will run:")
        for command in commands:
            self.say(f"    {command if isinstance(command, str) else ' '.join(command)}")
        if self.assume_yes:
            return True
        if self.interactive:
            hint = "[Y/n]" if default_yes else "[y/N]"
            try:
                answer = input(f"  {question} {hint} ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "n"
            if answer in ("y", "yes") or (default_yes and answer == ""):
                return True
        self.declined.extend(command if isinstance(command, str) else " ".join(command) for command in commands)
        self.say("  Skipped.")
        return False

    def execute(self, command, *, cwd=None):
        try:
            completed = subprocess.run(command, cwd=cwd, check=False)
        except OSError as error:
            raise SetupError(f"Could not run {command[0]}: {error.strerror}.") from None
        if completed.returncode:
            raise SetupError(f"{' '.join(command)} exited with status {completed.returncode}.")

    # Python packages

    def postgres_packages(self):
        self.say("==> PostgreSQL Python packages")
        try:
            for module in doctor.POSTGRES_MODULES:
                __import__(module)
            self.say("  Installed.")
            return True
        except ImportError:
            pass
        wanted = [entry.split(";")[0].strip() for entry in requires("research-multiplier") or ()
                  if 'extra == "postgres"' in entry.replace("'", '"')]
        if not wanted:
            raise SetupError("Install the PostgreSQL extra: pip install 'research-multiplier[postgres]'")
        command = [sys.executable, "-m", "pip", "install", *wanted]
        if not self.confirm("Install them into this Python environment?", [command]):
            return False
        self.execute(command)
        invalidate_caches()
        return True

    # Database

    def database(self):
        self.say("==> Database")
        if credentials.configured_in_environment():
            check = doctor.Doctor()
            check.workspace = self.workspace
            check.postgres_extra()
            check.connections()
            check.binding()
            if check.store is not None:
                check.store.__exit__(None, None, None)
            if check.status("connections") == doctor.OK and check.status("binding") == doctor.OK:
                self.say("  Already set up; the role logins work and the workspace is bound.")
                return
            if not credentials.read(paths.workspace_id(self.workspace)):
                raise SetupError("RM_*_DSN settings in your environment point to a database that does not work "
                                 "for this workspace. Unset them so setup can manage this project's logins.")
            for key in credentials.KEYS:
                os.environ.pop(key, None)
        admin = self.admin_connection()
        try:
            values = self.provision(admin)
        finally:
            admin.close()
        path = credentials.write(paths.workspace_id(self.workspace), values)
        os.environ.update(values)
        self.say(f"  Saved the project logins to {path} (readable only by you).")
        from research_multiplier.storage.postgres import bind_workspace

        identifier = bind_workspace(self.workspace)
        self.say(f"  Bound this workspace to database {identifier}.")

    def _connect(self, **settings):
        import psycopg

        settings = {key: value for key, value in settings.items() if value not in (None, "")}
        dsn = os.environ.get("RM_ADMIN_DSN")
        try:
            if dsn and not settings.get("password"):
                return psycopg.connect(dsn, autocommit=True, connect_timeout=5)
            return psycopg.connect(autocommit=True, connect_timeout=5, dbname="postgres", **settings)
        except psycopg.Error as error:
            text = str(error).lower()
            rejected = ("password", "authentication", "does not exist")
            if any(word in text for word in rejected) or getattr(error, "sqlstate", None) in ("28P01", "28000"):
                return "auth"
            if "connection refused" in text or "no such file" in text or "could not connect" in text:
                return "unreachable"
            return "failed"

    def admin_connection(self):
        target = {"host": self.host, "port": self.port, "user": self.admin_user}
        where = f"{self.host or 'the default PostgreSQL socket'}" + (f":{self.port}" if self.port else "")
        result = self._connect(**target)
        # Offer a local server only when no specific server was requested.
        explicit = self.host or self.port or os.environ.get("RM_ADMIN_DSN") or os.environ.get("PGHOST")
        if result == "unreachable" and not explicit and self.install_server():
            deadline = monotonic() + 20
            while result == "unreachable" and monotonic() < deadline:
                sleep(1)
                result = self._connect(**target)
        if result in ("auth", "failed") and not self.admin_user and not os.environ.get("RM_ADMIN_DSN"):
            # Many installers create the superuser "postgres" rather than one named after you.
            retry = self._connect(**{**target, "user": "postgres"})
            if not isinstance(retry, str) or retry == "auth":
                result, target = retry, {**target, "user": "postgres"}
        if result in ("auth", "failed") and self.interactive:
            self.say(f"  The PostgreSQL server at {where} needs an administrator login.")
            try:
                user = target["user"] or input("  Administrator user [postgres]: ").strip() or "postgres"
                password = getpass("  Password (not saved): ")
            except (EOFError, KeyboardInterrupt):
                raise SetupError("An administrator login is needed to create the project database.") from None
            result = self._connect(**{**target, "user": user, "password": password})
        if isinstance(result, str):
            reasons = {
                "unreachable": f"No PostgreSQL server answered at {where}. Start your server, or pass --host/--port.",
                "auth": "The administrator login was rejected. Pass --admin-user, set PGPASSWORD, or set RM_ADMIN_DSN.",
                "failed": f"Could not connect to PostgreSQL at {where}.",
            }
            raise SetupError(reasons[result])
        return result

    def install_server(self):
        if platform.system() != "Darwin" or shutil.which("brew") is None:
            self.say("  No PostgreSQL server is running. Install PostgreSQL 18 with pgvector and start it, "
                     "then run setup again.")
            return False
        self.say("  No PostgreSQL server is running. Homebrew can install and start PostgreSQL 18 with pgvector.")
        commands = [["brew", "install", "postgresql@18", "pgvector"], ["brew", "services", "start", "postgresql@18"]]
        if not self.confirm("Install and start PostgreSQL 18?", commands):
            return False
        for command in commands:
            self.execute(command)
        return True

    def check_server(self, admin):
        row = admin.execute("""
            SELECT current_setting('server_version_num')::int AS number, current_setting('server_version') AS name,
                   (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) AS superuser,
                   (SELECT default_version FROM pg_available_extensions WHERE name = 'vector') AS vector,
                   current_setting('data_directory', true) AS data
        """).fetchone()
        number, name, superuser, vector, data = (_text(value) for value in row)
        self.say(f"  Connected to PostgreSQL {name} as {admin.info.user}.")
        if number < MIN_SERVER:
            raise SetupError(f"This server runs PostgreSQL {name}. Research Multiplier needs PostgreSQL 18 or later "
                             "with pgvector. Point setup at a PostgreSQL 18 server with --host/--port.")
        if not superuser:
            raise SetupError(f"{admin.info.user} is not a superuser. Setup needs one to create the project roles, "
                             "database and pgvector extension. Pass --admin-user.")
        if vector is None:
            prefix = shutil.which("brew") and subprocess.run(["brew", "--prefix"], capture_output=True, text=True,
                                                             check=False).stdout.strip()
            if prefix and data and data.startswith(prefix):
                if self.confirm("pgvector is missing. Install it with Homebrew?", [["brew", "install", "pgvector"]]):
                    self.execute(["brew", "install", "pgvector"])
                    return
            raise SetupError("This PostgreSQL server does not have pgvector. Install pgvector for this server, "
                             "then run setup again.")

    def provision(self, admin):
        from psycopg import sql

        self.check_server(admin)
        short = str(paths.workspace_id(self.workspace)).replace("-", "")[:12]
        database = f"rm_{short}"
        owner = f"rm_{short}_owner"
        logins = {group: f"rm_{short}_{group}" for group in GROUPS}
        passwords = {name: secrets.token_hex(24) for name in (owner, *logins.values())}

        def role_exists(name):
            return admin.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,)).fetchone() is not None

        for group in GROUPS:
            if not role_exists(f"rm_{group}"):
                admin.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(f"rm_{group}")))
        for name in (owner, *logins.values()):
            verb = "ALTER" if role_exists(name) else "CREATE"
            admin.execute(sql.SQL(verb + " ROLE {} LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}")
                          .format(sql.Identifier(name), sql.Literal(passwords[name])))
        for group, login in logins.items():
            admin.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(f"rm_{group}"), sql.Identifier(login)))
        if admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,)).fetchone() is None:
            admin.execute(sql.SQL("CREATE DATABASE {} OWNER {} ENCODING 'UTF8' TEMPLATE template0")
                          .format(sql.Identifier(database), sql.Identifier(owner)))
            self.say(f"  Created database {database}.")
        else:
            self.say(f"  Reusing database {database}.")
        # Logins of other projects on this server share the capability groups, so they must not connect here.
        admin.execute(sql.SQL("REVOKE CONNECT, TEMPORARY ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database)))
        admin.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            sql.Identifier(database), sql.SQL(", ").join(sql.Identifier(name) for name in logins.values())))

        import psycopg

        host, port = admin.info.host, admin.info.port
        dsn = lambda name: _dsn(name, passwords[name], host, port, database)
        with psycopg.connect(autocommit=True, connect_timeout=5, host=host, port=port, dbname=database,
                             user=admin.info.user, password=admin.info.password or None) as connection:
            connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
        from research_multiplier.storage.migrate import migration_head, upgrade

        with psycopg.connect(dsn(owner), connect_timeout=5) as connection:
            migrated = connection.execute("SELECT to_regclass('public.rm_alembic_version')").fetchone()[0]
            revision = migrated and connection.execute("SELECT version_num FROM public.rm_alembic_version").fetchone()
        if not migrated:
            self.say("  Creating the research schema...")
            upgrade(dsn(owner))
        elif revision and revision[0] != migration_head():
            self.say(f"  Keeping the existing schema {revision[0]}. Back up the project before upgrading it "
                     f"to {migration_head()}.")
        values = {f"RM_{group.upper()}_DSN": dsn(login) for group, login in logins.items()}
        values["RM_MIGRATION_DSN"] = dsn(owner)
        return values

    # Lean

    def lean(self):
        self.say("==> Lean")
        system = platform.system()
        if not (system == "Darwin" or (system == "Linux" and platform.libc_ver()[0] == "glibc")):
            self.say("  Native Lean checks need macOS or a glibc-based Linux. Skipped.")
            return
        from research_multiplier.cli.lean_cli import _prepare

        project = self.workspace / "lean-project"
        _prepare(None, project)
        self.say(f"  Pinned Lean project ready at {project}.")
        elan = shutil.which("elan") or _existing(Path.home() / ".elan" / "bin" / "elan")
        if elan is None:
            if system == "Darwin" and shutil.which("brew"):
                commands = [["brew", "install", "elan-init"]]
            else:
                commands = [f"curl -sSf {ELAN_SCRIPT} | sh -s -- -y --default-toolchain none"]
            if not self.confirm("Install elan, the Lean version manager?", commands):
                return
            for command in commands:
                self.execute(command if isinstance(command, list) else ["sh", "-c", command])
            elan = shutil.which("elan") or _existing(Path.home() / ".elan" / "bin" / "elan")
            if elan is None:
                raise SetupError("elan was installed but is not on PATH. Add ~/.elan/bin to PATH and run setup again.")
        listing = subprocess.run([elan, "toolchain", "list"], capture_output=True, text=True, check=False).stdout
        if not any(line.split()[0].endswith("v4.19.0") for line in listing.splitlines() if line.strip()):
            command = [elan, "toolchain", "install", doctor.LEAN_TOOLCHAIN]
            if not self.confirm("Install Lean 4.19.0 (a few hundred MB)?", [command]):
                return
            self.execute(command)
        default = doctor.elan_default(elan)
        if not (default or "").endswith("v4.19.0"):
            current = f"elan's default is {default}" if default else "elan has no default toolchain"
            self.say(f"  {current}; Lean Core checks without --lean use the default.")
            command = [elan, "default", doctor.LEAN_TOOLCHAIN]
            self.confirm("Make Lean 4.19.0 the default?", [command], default_yes=default is None) and self.execute(command)
        else:
            self.say("  Lean 4.19.0 is installed and is the default.")
        built = project / ".lake" / "packages" / "mathlib" / ".lake" / "build" / "lib" / "lean" / doctor.MATHLIB_SAMPLE
        if built.is_file():
            self.say("  Compiled Mathlib is present.")
            return
        lake = shutil.which("lake") or _existing(Path(elan).parent / "lake")
        command = [lake or "lake", "exe", "cache", "get"]
        if not self.mathlib and (self.assume_yes or not self.interactive):
            self.say(f"  Compiled Mathlib is optional. To add it later (several GB): cd {project} && lake exe cache get")
            return
        if self.mathlib or self.confirm("Download compiled Mathlib (optional, several GB)?", [command],
                                        default_yes=False):
            self.execute(command, cwd=project)

    def run(self):
        problems = []
        with paths.use_workspace(self.workspace, strict=True):
            credentials.load(self.workspace)
            steps = [lambda: self.postgres_packages() and self.database()]
            if not self.skip_lean:
                steps.append(self.lean)
            for step in steps:
                # A failed step is reported and the remaining steps still run.
                try:
                    step()
                except ValueError as error:
                    problems.append(str(error))
                    self.say(f"  Problem: {error}")
            self.say("\n==> Check")
            report = doctor.diagnose(skip_lean=self.skip_lean)
        self.say(doctor.render(report))
        if problems:
            self.say("\nSetup could not finish:")
            for problem in problems:
                self.say(f"  {problem}")
        if self.declined:
            self.say("\nSkipped steps. Run these yourself, or rerun setup with --yes:")
            for command in self.declined:
                self.say(f"  {command}")
        return report


def _text(value):
    # SQL_ASCII databases return text settings as bytes.
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value


def _existing(path):
    return str(path) if Path(path).is_file() else None


def _dsn(user, password, host, port, database):
    if host and host.startswith("/"):
        location = quote(host, safe="")
    elif host and ":" in host:
        location = f"[{host}]"
    else:
        location = host or "localhost"
    return f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}@{location}:{port}/{quote(database, safe='')}"


def choose_workspace(location):
    if location is not None:
        return paths.initialize_workspace(location)
    current = paths.active_workspace()
    if current is not None:
        return current
    if not sys.stdin.isatty():
        raise SetupError("Supply the research workspace folder: research-multiplier setup PATH")
    try:
        value = input("Choose the folder for this research workspace. Path: ").strip()
    except (EOFError, KeyboardInterrupt):
        value = ""
    if not value:
        raise SetupError("A research workspace folder is required.")
    return paths.initialize_workspace(Path(value))
