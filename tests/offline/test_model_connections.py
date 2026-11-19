import json
from pathlib import Path
from subprocess import CompletedProcess, DEVNULL

import pytest

from research_multiplier.model_adapters import AdapterReply
from research_multiplier.models import cli_executor, connections
from research_multiplier.models.connection_secrets import CredentialStoreError
from research_multiplier.models.model_errors import ModelFailure


pytestmark = pytest.mark.offline


class Secrets:
    def __init__(self):
        self.values = {}

    def add(self, identifier, value):
        self.values[identifier] = value

    def get(self, identifier):
        return self.values.get(identifier)

    def remove(self, identifier):
        self.values.pop(identifier, None)


def test_added_connections_are_the_only_saved_profiles_and_keys_stay_separate(tmp_path):
    location = tmp_path / "settings" / "connections.sqlite3"
    secrets = Secrets()
    registry = connections.Registry(location, secrets=secrets)
    assert registry.list() == []
    assert not location.exists()

    request = {"provider": "anthropic", "model": "test-claude", "label": "Work", "api_key": "test-secret-api-key"}
    added = registry.add(request)
    saved = connections.Registry(location, secrets=secrets)
    assert saved.list() == [added]
    assert added["status"] == "untested"
    assert secrets.get(added["id"]) == request["api_key"]
    assert request["api_key"].encode() not in location.read_bytes()
    config, identity = saved.resolve(added["id"])
    assert config.api_key == request["api_key"]
    assert config.base_url == "https://api.anthropic.com/v1"
    assert identity == saved.identity(added["id"])
    assert identity["fingerprint"] == added["fingerprint"]
    assert not ({"api_key", "binary", "label"} & identity.keys())

    with registry.use(added["id"]):
        with pytest.raises(connections.ConnectionError, match=r"connection\.busy"):
            saved.remove(added["id"])
    saved.remove(added["id"])
    saved.remove(added["id"])
    assert not registry.list() and not secrets.values
    with pytest.raises(connections.ConnectionError, match=r"connection\.missing"):
        registry.config(added["id"])
    replacement = registry.add(request)
    assert replacement["id"] != added["id"]
    assert replacement["fingerprint"] != added["fingerprint"]


def test_keychain_failure_rolls_back_saved_descriptor(tmp_path):
    class Unavailable(Secrets):
        def add(self, identifier, value):
            raise CredentialStoreError("Secret details are not safe to show.")

    registry = connections.Registry(tmp_path / "connections.sqlite3", secrets=Unavailable())
    with pytest.raises(connections.ConnectionError, match=r"^connection\.keychain_unavailable$"):
        registry.add({"provider": "openai", "model": "test-gpt", "api_key": "test-failed-secret"})
    assert registry.list() == []


@pytest.mark.parametrize("provider,command", [("codex-cli", "codex"), ("claude-code", "claude")])
def test_native_binary_is_part_of_the_authorized_identity(provider, command, tmp_path, monkeypatch):
    binary = tmp_path / command
    monkeypatch.setattr(connections.shutil, "which", lambda value, **kwargs: str(binary) if binary.exists() else None)
    registry = connections.Registry(tmp_path / "connections.sqlite3")
    row = registry.add({"provider": provider, "model": "test-model", "binary": str(binary)})
    assert row["fingerprint"] is None
    assert row["status"] == "connection.missing_cli"

    binary.write_text("first executable")
    binary.chmod(0o700)
    before = registry.identity(row["id"])
    assert before["fingerprint"] == registry.list()[0]["fingerprint"]
    replacement = tmp_path / "replacement"
    replacement.write_text("different binary")
    replacement.chmod(0o700)
    replacement.replace(binary)
    assert registry.identity(row["id"])["fingerprint"] != before["fingerprint"]


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_connection_test_is_synthetic_and_sanitizes_failure(provider, tmp_path, monkeypatch):
    probe_calls = []
    outcome = [AdapterReply("reachable", "model-from-provider")]

    class Adapter:
        def complete(self, request):
            probe_calls.append(request)
            if isinstance(outcome[0], ModelFailure):
                raise outcome[0]
            return outcome[0]

    def get_adapter(config, timeout, post):
        assert config.provider == provider and config.api_key == "fake-key"
        assert timeout == 45 and callable(post)
        return Adapter()

    monkeypatch.setattr(connections, "_adapter", get_adapter)
    registry = connections.Registry(tmp_path / "connections.sqlite3", secrets=Secrets())
    row = registry.add({"provider": provider, "model": "test-model", "api_key": "fake-key"})
    assert not probe_calls
    assert registry.test(row["id"]) == {"status": "connected", "provider": provider, "model": "test-model"}
    assert len(probe_calls) == 1
    assert probe_calls[0]["messages"] == [
        {"role": "system", "content": 'Return exactly the JSON object {"reachable":true}.'},
        {"role": "user", "content": "{}"},
    ]
    assert probe_calls[0]["budget"] == {"wall_seconds": 45, "max_attempts": 1, "max_model_calls": 1,
                                       "max_tokens": 256}
    outcome[0] = ModelFailure("authentication", "Provider body with fake-key")
    assert registry.test(row["id"])["status"] == "connection.authentication"
    assert registry.list()[0]["status"] == "connection.authentication"
    assert "fake-key" not in repr(registry.list())


@pytest.mark.parametrize("provider,command", [("codex-cli", ("login", "status")), ("claude-code", ("auth", "status"))])
def test_native_login_check_never_receives_project_credentials(provider, command, monkeypatch):
    monkeypatch.setenv("RM_HUMAN_DSN", "TEST")
    monkeypatch.setenv("OPENAI_API_KEY", "TEST")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "TEST")

    def run(arguments, *, cwd, env, timeout, stdout, stderr, check):
        assert arguments == ["/path/to/command", *command]
        assert Path(cwd).name.startswith("rm-proof-worker-")
        assert not any(key in env for key in ["RM_HUMAN_DSN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"])
        assert timeout == 5 and stdout == stderr == DEVNULL and not check
        return CompletedProcess(arguments, 1)

    monkeypatch.setattr(cli_executor.subprocess, "run", run)
    with pytest.raises(ModelFailure, match="Complete sign-in") as failure:
        cli_executor.require_native_login(provider, "/path/to/command")
    assert failure.value.code == "authentication"


@pytest.mark.parametrize("provider,signal,code", [
    ("codex-cli", 'provider HTTP 400 {"code":"model_not_found"}', "model_unavailable"),
    ("claude-code", "Too many requests. Usage limit.", "rate_limit"),
    ("claude-code", "Network error: connection refused", "transport"),
])
def test_native_failures_show_actionable_codes_without_process_output(provider, signal, code, monkeypatch):
    outcomes = iter([CompletedProcess([], 0, "test version\n", ""),
                     CompletedProcess([], 1, signal + " DO NOT SHOW THIS", "")])
    monkeypatch.setattr(cli_executor.subprocess, "run", lambda *args, **kw: next(outcomes))
    with pytest.raises(ModelFailure) as failure:
        cli_executor.complete(provider, "/path/to/cli", "test-model", [], 10)
    assert failure.value.code == code
    assert "DO NOT SHOW THIS" not in str(failure.value)


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_registered_codex_dispatch_stops_without_os_isolation(platform, monkeypatch):
    monkeypatch.setattr(cli_executor.sys, "platform", platform)
    monkeypatch.setattr(cli_executor.os, "access", lambda *args: False)
    monkeypatch.setattr(cli_executor.subprocess, "run", lambda *args, **kwargs: pytest.fail("unisolated dispatch"))
    with cli_executor.workbench_connection("codex-cli"):
        with pytest.raises(ModelFailure, match="macOS sandbox") as failure:
            cli_executor.complete("codex-cli", "/path/to/codex", "test-model", [], 10)
    assert failure.value.code == "configuration"


def test_registered_codex_blocks_global_instructions_and_skills_only_for_dispatch(tmp_path, monkeypatch):
    home = tmp_path / "user home"
    codex_home = tmp_path / 'custom "Codex" home'
    home.mkdir()
    codex_home.mkdir()
    linked = tmp_path / "linked user instructions.md"
    linked.write_text("Synthetic global instructions")
    (codex_home / "AGENTS.md").symlink_to(linked)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(cli_executor.Path, "home", lambda: home)
    monkeypatch.setattr(cli_executor.sys, "platform", "darwin")
    monkeypatch.setattr(cli_executor.os, "access", lambda *args: True)

    commands = []

    def run(arguments, **options):
        commands.append(arguments)
        assert options["env"]["CODEX_HOME"] == str(codex_home)
        if arguments[-1] == "--version":
            return CompletedProcess(arguments, 0, "fixture version", "")
        return CompletedProcess(arguments, 1, "synthetic failure", "")

    monkeypatch.setattr(cli_executor.subprocess, "run", run)
    messages = [{"role": "user", "content": "Synthetic request"}]
    with cli_executor.workbench_connection("codex-cli"), pytest.raises(ModelFailure):
        cli_executor.complete("codex-cli", "/path/to/codex", "test-model", messages, 10)
    with pytest.raises(ModelFailure):
        cli_executor.complete("codex-cli", "/path/to/codex", "test-model", messages, 10)

    _, guarded, _, legacy = commands
    assert guarded[:2] == ["/usr/bin/sandbox-exec", "-p"]
    assert f"(literal {json.dumps(str(linked.resolve()))})" in guarded[2]
    assert f"(literal {json.dumps(str(codex_home / 'AGENTS.override.md'))})" in guarded[2]
    assert f"(subpath {json.dumps(str(codex_home / 'skills'))})" in guarded[2]
    assert f"(subpath {json.dumps(str(home / '.agents' / 'skills'))})" in guarded[2]
    assert "project_doc_max_bytes=0" in guarded and guarded[-1] == "-"
    assert legacy[0] == "/path/to/codex" and "project_doc_max_bytes=0" not in legacy


@pytest.mark.parametrize("denied_document_notice", [True, False])
def test_registered_codex_accepts_only_the_sandbox_instruction_notice(denied_document_notice, monkeypatch):
    message = (
        "Failed to read global AGENTS.md instructions from `/test/AGENTS.md`: Operation not permitted (os error 1)"
        if denied_document_notice else "An unrelated error"
    )
    output = "\n".join(map(json.dumps, [
        {"type": "item.completed", "item": {"type": "error", "message": message}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": '{"reachable":true}'}},
        {"type": "turn.completed", "usage": {}},
    ]))
    results = iter([CompletedProcess([], 0, "fixture version", ""), CompletedProcess([], 0, output, "")])
    monkeypatch.setattr(cli_executor, "_isolated_codex_command", lambda command, _: command)
    monkeypatch.setattr(cli_executor.subprocess, "run", lambda *args, **kwargs: next(results))
    with cli_executor.workbench_connection("codex-cli"):
        if denied_document_notice:
            reply = cli_executor.complete("codex-cli", "/path/to/codex", "test-model", [], 10)
            assert reply["choices"][0]["message"]["content"] == '{"reachable":true}'
        else:
            with pytest.raises(ModelFailure, match="Codex attempted an external action."):
                cli_executor.complete("codex-cli", "/path/to/codex", "test-model", [], 10)


@pytest.mark.parametrize("status,body,expected", [
    (400, b'{"error":{"code":"model_not_found"}}', "model_unavailable"),
    (404, b'{"error":{"type":"not_found_error"}}', "model_unavailable"),
    (400, b'{"error":{"message":"provider-only details"}}', "request_rejected"),
])
def test_api_model_failures_have_safe_codes(status, body, expected, monkeypatch):
    import io
    from urllib.error import HTTPError
    from research_multiplier.models import model_http

    class Opener:
        def open(self, request, timeout):
            raise HTTPError(request.full_url, status, "Error details", {}, io.BytesIO(body))

    monkeypatch.setattr(model_http, "build_opener", lambda *args: Opener())
    with pytest.raises(ModelFailure) as failure:
        model_http.post_json("https://api.example.test/v1/messages", {}, {}, 1)
    assert failure.value.code == expected
    assert "provider-only details" not in str(failure.value)
