"""User-added desktop model connections, separate from per-workspace authority."""

from contextlib import closing, contextmanager
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import sys
from uuid import UUID, uuid4

from research_multiplier.model_adapters import PROVIDERS, ModelConfig, ModelRequest, _adapter, _validate_reply
from research_multiplier.models import cli_executor
from research_multiplier.models.connection_secrets import CredentialStoreError, Keychain
from research_multiplier.models.model_errors import ModelFailure
from research_multiplier.models.model_http import post_json
from research_multiplier.protocol.serialization import fingerprint
from research_multiplier.storage import credentials
from research_multiplier.storage.managed_workspaces import application_home


_NATIVE_PROVIDERS = {"codex-cli": "Codex", "claude-code": "Claude Code"}
SUPPORTED_PROVIDERS = frozenset({*_NATIVE_PROVIDERS, "openai", "anthropic"})
_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,254}")
_SCHEMA = """
    CREATE TABLE IF NOT EXISTS model_connections (
        id TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        label TEXT NOT NULL,
        binary TEXT,
        status TEXT NOT NULL DEFAULT 'untested',
        test_sequence INTEGER NOT NULL DEFAULT 0
    )
"""
_CONNECTION_FAILURES = frozenset({
    "authentication", "rate_limit", "transport", "timeout", "request_rejected", "model_unavailable",
    "invalid_output", "configuration",
    "missing_cli", "missing_key", "keychain_unavailable", "busy",
})


class ConnectionError(ValueError):
    """A fixed error code safe for the workbench API."""

    def __init__(self, reason):
        self.code = "connection." + reason
        super().__init__(self.code)


def provider_error_code(error, *, fallback):
    """Project a model failure onto the workbench's known provider errors."""
    if isinstance(error, ConnectionError):
        return error.code
    return "connection." + error.code if error.code in _CONNECTION_FAILURES else fallback


def _identifier(value):
    try:
        return str(UUID(value))
    except (AttributeError, TypeError, ValueError):
        raise ConnectionError("invalid") from None


def _text(value, maximum):
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(not char.isprintable() for char in value)):
        raise ConnectionError("invalid")
    return value.strip()


def _model_id(value):
    model = _text(value, 255)
    if not _MODEL_ID.fullmatch(model):
        raise ConnectionError("invalid")
    return model


def _native_model_choices(provider, binary):
    if provider == "claude-code":
        return [{"id": name, "label": name.title()} for name in cli_executor.CLAUDE_MODEL_ALIASES]
    choices = {}
    for row in cli_executor.codex_models(binary):
        try:
            model = _model_id(row.get("slug"))
            if model == cli_executor.NATIVE_DEFAULT_MODEL or model in choices:
                continue
            label = _text(row.get("display_name") or model, 255)
        except ConnectionError:
            continue
        choices[model] = {"id": model, "label": label}
        if len(choices) >= 100:
            break
    return list(choices.values())


def _input(document):
    if (not isinstance(document, dict) or not {"provider", "model"} <= document.keys()
            or document.keys() - {"provider", "model", "label", "binary", "api_key"}):
        raise ConnectionError("invalid")
    provider = _text(document["provider"], 40)
    model = _model_id(document["model"])
    if provider not in SUPPORTED_PROVIDERS:
        raise ConnectionError("invalid")
    label = _text(document["label"], 120) if "label" in document else model
    transport, command, _, _ = PROVIDERS[provider]
    binary, key = document.get("binary"), document.get("api_key")
    if transport == "cli":
        if key is not None or model == cli_executor.NATIVE_DEFAULT_MODEL:
            raise ConnectionError("invalid")
        if binary is not None:
            binary = _text(binary, 4096)
            path = Path(binary)
            if not path.is_absolute() or path.name != command:
                raise ConnectionError("invalid")
    else:
        if binary is not None:
            raise ConnectionError("invalid")
        if not isinstance(key, str) or not re.fullmatch(r"[\x21-\x7e]{1,4096}", key):
            raise ConnectionError("missing_key")
    return provider, model, label, binary, key


def _binary(provider, selected):
    if selected:
        return shutil.which(selected)
    command = PROVIDERS[provider][1]
    directories = [str(Path.home() / ".local" / "bin"), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    directories.extend(folder for folder in os.environ.get("PATH", "").split(os.pathsep) if os.path.isabs(folder))
    return shutil.which(command, path=os.pathsep.join(dict.fromkeys(directories)))


def _descriptor(row):
    provider = row["provider"]
    transport, endpoint, family, _ = PROVIDERS[provider]
    binary = version = None
    if transport == "cli":
        binary = _binary(provider, row["binary"])
        if not binary:
            raise ConnectionError("missing_cli")
        try:
            executable = Path(binary).stat()
        except OSError:
            raise ConnectionError("missing_cli") from None
        endpoint = ""
        version = (executable.st_dev, executable.st_ino, executable.st_size, executable.st_mtime_ns)
    config = {"provider": provider, "model": row["model"], "family": family, "base_url": endpoint, "binary": binary}
    digest = fingerprint({"id": row["id"], **config, "executable_identity": version})
    identity = {key: row[key] for key in ("id", "provider", "model")} | {"fingerprint": digest}
    return config, identity


def _public(row):
    status = row["status"]
    try:
        _, identity = _descriptor(row)
    except ConnectionError as error:
        identity = {key: row[key] for key in ("id", "provider", "model")} | {"fingerprint": None}
        status = error.code
    return {**identity, "label": row["label"], "status": status}

def _validate_metadata(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ConnectionError("unavailable")
    if os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise ConnectionError("unavailable")


def _test(config):
    transport = PROVIDERS[config.provider][0]
    if transport == "cli":
        cli_executor.require_native_login(config.provider, config.binary)
    timeout = 45
    request = ModelRequest(
        contract_version=1, model_id=config.model,
        messages=[{"role": "system", "content": "Return exactly the JSON object {\"reachable\":true}."},
                  {"role": "user", "content": "{}"}],
        parameters={"timeout_seconds": timeout, "requested_model": config.model},
        budget={"wall_seconds": timeout, "max_attempts": 1, "max_model_calls": 1, "max_tokens": 256},
    )
    # This explicit connection check uses a constant message and no project
    # context, project budget, artifacts, logs, or provider response storage.
    reply = _adapter(config, timeout, post_json).complete(request)
    _validate_reply(reply)
    if reply.finish_reason not in {"stop", "end_turn", "stop_sequence"} or not reply.text.strip():
        raise ModelFailure("invalid_output", "Connection check returned no complete response.")
    return reply.model_id if reply.model_id_reported else None


def _settings_file():
    legacy = credentials.directory().parent / "model-connections.sqlite3"
    if sys.platform != "darwin":
        return legacy
    installed = application_home() / "Settings" / legacy.name
    if not installed.exists() and legacy.exists():
        return legacy
    return installed


class Registry:
    """Descriptors are immutable, so removing and re-adding a connection changes its ID."""

    def __init__(self, settings_file=None, *, secrets=None):
        self._file = Path(settings_file) if settings_file is not None else _settings_file()
        self._secrets = secrets if secrets is not None else Keychain()

    @contextmanager
    def _write(self):
        try:
            self._file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            try:
                descriptor = os.open(self._file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(descriptor)
            _validate_metadata(self._file)
            with closing(sqlite3.connect(self._file, timeout=2)) as database, database:
                database.row_factory = sqlite3.Row
                database.execute("BEGIN IMMEDIATE")
                database.execute(_SCHEMA)
                yield database
        except (OSError, sqlite3.Error):
            raise ConnectionError("unavailable") from None

    def _rows(self, identifier=None):
        try:
            _validate_metadata(self._file)
            with closing(sqlite3.connect(self._file.absolute().as_uri() + "?mode=ro", uri=True, timeout=2)) as database:
                database.row_factory = sqlite3.Row
                if database.execute("SELECT 1 FROM sqlite_master WHERE name = 'model_connections'").fetchone() is None:
                    return []
                if identifier is not None:
                    return database.execute("SELECT * FROM model_connections WHERE id = ?", (identifier,)).fetchall()
                return database.execute("SELECT * FROM model_connections ORDER BY label, provider, model, id").fetchall()
        except FileNotFoundError:
            return []
        except (OSError, sqlite3.Error):
            raise ConnectionError("unavailable") from None

    def _find(self, identifier):
        rows = self._rows(_identifier(identifier))
        if not rows:
            raise ConnectionError("missing")
        return rows[0]

    def list(self):
        return [_public(row) for row in self._rows()]

    def native(self):
        """Discover installed CLI tools and read their sign-in status."""
        providers = []
        for provider in _NATIVE_PROVIDERS:
            binary = _binary(provider, None)
            if not binary:
                continue
            try:
                cli_executor.require_native_login(provider, binary)
            except ModelFailure as error:
                status = provider_error_code(error, fallback="connection.unavailable")
            else:
                status = "signed_in"
            models = _native_model_choices(provider, binary) if status == "signed_in" else []
            providers.append({"provider": provider, "status": status, "models": models})
        return providers

    def add_native(self, document):
        """Select one discovered tool using its existing native sign-in."""
        if not isinstance(document, dict) or "provider" not in document or document.keys() - {"provider", "model"}:
            raise ConnectionError("invalid")
        provider = _text(document["provider"], 40)
        if provider not in _NATIVE_PROVIDERS:
            raise ConnectionError("invalid")
        model = _model_id(document.get("model", cli_executor.NATIVE_DEFAULT_MODEL))
        binary = _binary(provider, None)
        if not binary:
            raise ConnectionError("missing_cli")
        try:
            cli_executor.require_native_login(provider, binary)
        except ModelFailure as error:
            status = provider_error_code(error, fallback="connection.unavailable")
            raise ConnectionError(status.removeprefix("connection.")) from None
        return self._add(provider, model, _NATIVE_PROVIDERS[provider], binary, None, deduplicate=True)

    def add(self, document):
        return self._add(*_input(document))

    def _add(self, provider, model, label, binary, key, *, deduplicate=False):
        identifier = str(uuid4())
        saved = False
        try:
            with self._write() as database:
                if deduplicate:
                    existing = database.execute(
                        "SELECT * FROM model_connections WHERE provider = ? AND model = ? AND binary = ? LIMIT 1",
                        (provider, model, binary),
                    ).fetchone()
                    if existing is not None:
                        return _public(existing)
                database.execute(
                    "INSERT INTO model_connections (id, provider, model, label, binary) VALUES (?, ?, ?, ?, ?)",
                    (identifier, provider, model, label, binary),
                )
                if key is not None:
                    self._secret("add", identifier, key)
                    saved = True
        except Exception:
            if saved:
                self._secret("remove", identifier)
            raise
        return _public(self._find(identifier))

    @contextmanager
    def _dispatch_lock(self, *, exclusive):
        try:
            import fcntl
        except ImportError:
            raise ConnectionError("unavailable") from None
        lock_file = self._file.with_suffix(".lock")
        try:
            _validate_metadata(self._file)
            descriptor = os.open(lock_file, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        except FileNotFoundError:
            raise ConnectionError("missing") from None
        except OSError:
            raise ConnectionError("unavailable") from None
        # Keep the lock inode adjacent to the registry. SQLite itself uses
        # kernel file locks, so acquire the application lease on a peer file.
        with os.fdopen(descriptor, "rb") as file:
            mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            try:
                fcntl.flock(file.fileno(), mode | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ConnectionError("busy") from None
            except OSError:
                raise ConnectionError("unavailable") from None
            yield

    def remove(self, identifier):
        identifier = _identifier(identifier)
        if not self._rows(identifier):
            return
        with self._dispatch_lock(exclusive=True), self._write() as database:
            row = database.execute("SELECT provider FROM model_connections WHERE id = ?", (identifier,)).fetchone()
            if row is not None:
                if PROVIDERS[row["provider"]][0] != "cli":
                    self._secret("remove", identifier)
                database.execute("DELETE FROM model_connections WHERE id = ?", (identifier,))

    def _secret(self, action, *arguments):
        try:
            return getattr(self._secrets, action)(*arguments)
        except CredentialStoreError:
            raise ConnectionError("keychain_unavailable") from None

    def resolve(self, identifier):
        """Bind the exact selected config to a secret-free identity for review and dispatch."""
        row = self._find(identifier)
        config, identity = _descriptor(row)
        if config["binary"] is None:
            key = self._secret("get", row["id"])
            if not key:
                raise ConnectionError("missing_key")
            config["api_key"] = key
        try:
            return ModelConfig(**config), identity
        except ModelFailure:
            raise ConnectionError("configuration") from None

    def config(self, identifier):
        return self.resolve(identifier)[0]

    def identity(self, identifier):
        return _descriptor(self._find(identifier))[1]

    @contextmanager
    def use(self, identifier):
        """Load one connection for dispatch and prevent removal until that call ends."""
        with self._dispatch_lock(exclusive=False):
            config, identity = self.resolve(identifier)
            with cli_executor.workbench_connection(config.provider):
                yield config, identity

    def test(self, identifier):
        identifier = _identifier(identifier)
        with self._write() as database:
            row = database.execute("SELECT * FROM model_connections WHERE id = ?", (identifier,)).fetchone()
            if row is None:
                raise ConnectionError("missing")
            sequence = row["test_sequence"] + 1
            database.execute("UPDATE model_connections SET test_sequence = ? WHERE id = ?", (sequence, identifier))
        result = {"provider": row["provider"], "model": row["model"]}
        try:
            with self.use(identifier) as (config, _):
                reported_model = _test(config)
        except (ConnectionError, ModelFailure) as error:
            status = provider_error_code(error, fallback="connection.unavailable")
        else:
            status = "connected"
            if row["provider"] in _NATIVE_PROVIDERS:
                result["reported_model"] = reported_model
        with self._write() as database:
            database.execute("UPDATE model_connections SET status = ? WHERE id = ? AND test_sequence = ?",
                             (status, identifier, sequence))
        return {**result, "status": status}
