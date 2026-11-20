"""Stateless CLI workers for public proof and audit packets."""

from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic

from .model_errors import ModelFailure


# Native credentials stay owned by the installed CLI. Never pass project DSNs,
# API keys, or project paths to either a login check or an inference command.
_NATIVE_ENV = {"HOME", "PATH", "USER", "LOGNAME", "TMPDIR", "SYSTEMROOT", "APPDATA", "LOCALAPPDATA",
               "USERPROFILE", "LANG", "LC_ALL", "CODEX_HOME", "CLAUDE_CONFIG_DIR"}
NATIVE_DEFAULT_MODEL = "native-default"
_WORKBENCH_PROVIDER = ContextVar("workbench_cli_provider", default=None)
_MAC_SANDBOX = "/usr/bin/sandbox-exec"
_CODEX_INSTRUCTIONS = ("AGENTS.md", "AGENTS.override.md")


def _environment():
    return {key: value for key, value in os.environ.items() if key in _NATIVE_ENV}


@contextmanager
def workbench_connection(provider):
    """Keep stricter native limits scoped to user-added workbench connections."""
    token = _WORKBENCH_PROVIDER.set(provider)
    try:
        yield
    finally:
        _WORKBENCH_PROVIDER.reset(token)


def _require_mac_sandbox(provider):
    if sys.platform != "darwin" or not os.access(_MAC_SANDBOX, os.X_OK):
        raise ModelFailure("configuration", f"A registered {provider} connection needs the macOS sandbox.")


def _sandbox_rules(matcher, locations):
    rules = []
    for location in locations:
        for path in sorted({str(location.absolute()), str(location.resolve())}):
            if not path.isprintable():
                raise ValueError
            rules.append(f"({matcher} {json.dumps(path, ensure_ascii=False)})")
    return " ".join(rules)


def _isolated_claude_command(command, environment, directory):
    _require_mac_sandbox("Claude Code")
    from research_multiplier.storage import paths

    try:
        home = Path.home()
        config = Path(environment.get("CLAUDE_CONFIG_DIR", str(home / ".claude")))
        binary = Path(command[0])
        if not config.is_absolute() or not binary.is_absolute():
            raise ValueError
        denied = [Path("/Users"), home, Path("/Volumes"), Path("/private/tmp"), Path("/private/var/folders")]
        allowed = [Path(directory), home / "Library" / "Keychains"]
        files = [binary, home / ".claude.json", config / ".claude.json", config / ".credentials.json",
                 config / "settings.json"]
        # The official npm distribution may use a script and load its package.
        for parent in binary.resolve().parents:
            if parent.name == "claude-code" and parent.parent.name == "@anthropic-ai":
                allowed.append(parent)
                node = shutil.which("node", path=environment.get("PATH", ""))
                if node:
                    files.append(Path(node))
                break
        block = _sandbox_rules("subpath", denied)
        readable = " ".join((_sandbox_rules("subpath", allowed), _sandbox_rules("literal", files)))
        profile = f"(version 1)(allow default)(deny file-read-data {block})(allow file-read-data {readable})"
        workspace = paths.active_workspace()
        if workspace is not None:
            protected = workspace.parent if workspace.name == "Trellin" else workspace
            profile += f'(deny file-read* file-write* {_sandbox_rules("subpath", [protected])})'
    except (OSError, RuntimeError, ValueError):
        raise ModelFailure("configuration", "Could not isolate the registered Claude Code connection.") from None
    # Managed hooks still run in Claude Code safe mode. Keep user files outside
    # the process's read boundary even if those hooks start another process.
    return [_MAC_SANDBOX, "-p", profile, *command]


def _isolated_codex_command(command, environment):
    _require_mac_sandbox("Codex")
    try:
        home = Path.home()
        configured = environment.get("CODEX_HOME")
        codex = Path(configured) if configured else home / ".codex"
        if not codex.is_absolute():
            raise ValueError
        instructions = [codex / name for name in _CODEX_INSTRUCTIONS]
        skills = [home / ".agents" / "skills", codex / "skills", codex / "plugins"]
        rules = " ".join((_sandbox_rules("literal", instructions), _sandbox_rules("subpath", skills)))
    except (OSError, RuntimeError, ValueError):
        raise ModelFailure("configuration", "Could not isolate the registered Codex connection.") from None
    profile = f"(version 1)(allow default)(deny file-read* {rules})"
    return [
        _MAC_SANDBOX, "-p", profile, *command[:-1],
        "-c", "project_doc_max_bytes=0", "--disable", "plugins", "--disable", "browser_use",
        "--disable", "computer_use", "--disable", "tool_suggest", "--disable", "skill_search",
        "--disable", "view_image", "--disable", "image_generation", command[-1],
    ]


def require_native_login(provider, binary):
    """Check the official CLI's own login without sending a prompt."""
    if provider == "codex-cli":
        status = ["login", "status"]
    elif provider == "claude-code":
        status = ["auth", "status"]
    else:
        raise ModelFailure("configuration", "Unknown native CLI model provider.")
    try:
        with TemporaryDirectory(prefix="rm-proof-worker-") as directory:
            result = subprocess.run([binary, *status], cwd=directory, env=_environment(), timeout=5,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, check=False)
    except subprocess.TimeoutExpired:
        raise ModelFailure("timeout", "Native CLI login check timed out.") from None
    except OSError:
        raise ModelFailure("adapter", "Native CLI login check could not start.") from None
    if result.returncode == 1:
        raise ModelFailure("authentication", "Complete sign-in with the native CLI.")
    if result.returncode != 0:
        raise ModelFailure("adapter", "Native CLI could not check its authentication status.")


def _failed_completion(provider, signal):
    """Map known transport failures without retaining native process output."""
    signal = signal.casefold()
    if any(word in signal for word in ("not logged in", "please run /login", "authenticate", "unauthorized")):
        return ModelFailure("authentication", f"{provider} is not signed in. Complete login using the native CLI.")
    if any(word in signal for word in ("rate limit", "too many requests", "usage limit", "quota", "out of credits")):
        return ModelFailure("rate_limit", f"{provider} reached a rate or usage limit.", retryable=True)
    if any(word in signal for word in ("model_not_found", "model not found", "model is not available",
                                      "model is not supported", "unsupported model", "no access to model")):
        return ModelFailure("model_unavailable", f"The selected model is unavailable from {provider}.")
    if any(word in signal for word in ("failed to connect", "connection refused", "network error",
                                      "network unreachable", "stream disconnected", "service unavailable")):
        return ModelFailure("transport", f"{provider} could not reach the model service.", retryable=True)
    return None


def _blocked_global_instruction_notice(item):
    if _WORKBENCH_PROVIDER.get() != "codex-cli" or item["type"] != "error":
        return False
    message = item.get("message")
    return isinstance(message, str) and message.startswith("Failed to read global AGENTS.md instructions from `") and any(
        message.endswith(f"/{name}`: Operation not permitted (os error 1)") for name in _CODEX_INSTRUCTIONS
    )


def complete(provider, binary, model, messages, timeout, *, reasoning_effort=None):
    if reasoning_effort is not None and provider != "codex-cli":
        raise ModelFailure("configuration", f"The {provider} adapter does not implement reasoning effort.")
    deadline = monotonic() + timeout
    prompt = "\n\n".join(f"{entry['role'].upper()}\n{entry['content']}" for entry in messages)
    workbench = _WORKBENCH_PROVIDER.get() == provider
    model_option = [] if workbench and model == NATIVE_DEFAULT_MODEL else ["--model", model]
    if provider == "codex-cli":
        command = [
            binary, "-a", "never", "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules",
            "--skip-git-repo-check", "--sandbox", "read-only", "--disable", "shell_tool",
            "--disable", "memories", "--disable", "apps", "--disable", "multi_agent", "--disable", "hooks",
            "-c", 'mcp_servers.notion={url="http://127.0.0.1:9",enabled=false}',
            "-c", 'web_search="disabled"', *model_option, "--color", "never", "--json", "-",
        ]
        if reasoning_effort is not None:
            command[4:4] = ["-c", f"model_reasoning_effort={json.dumps(reasoning_effort)}"]
    elif provider == "claude-code":
        command = [
            binary, "-p", "--safe-mode", "--restricted", "--tools", "", "--strict-mcp-config",
            "--mcp-config", '{"mcpServers":{}}', "--disallowedTools", "mcp__*",
            "--no-session-persistence", "--max-turns", "1", "--output-format", "json", *model_option,
        ]
    else:
        raise ModelFailure("configuration", "Unknown native CLI model provider.")

    environment = _environment()
    if provider == "codex-cli" and workbench:
        command = _isolated_codex_command(command, environment)
    try:
        with TemporaryDirectory(prefix="rm-proof-worker-") as directory:
            if provider == "claude-code" and workbench:
                command = _isolated_claude_command(command, environment, directory)
                environment["TMPDIR"] = directory
                # Claude's own temporary files use this setting rather than TMPDIR on macOS.
                environment["CLAUDE_CODE_TMPDIR"] = directory
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError
            version = subprocess.run([binary, "--version"], cwd=directory, env=environment,
                                     capture_output=True, text=True, timeout=min(10, remaining),
                                     check=True).stdout.strip()
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError
            child = subprocess.run(command, cwd=directory, env=environment, input=prompt,
                                   capture_output=True, text=True, timeout=remaining, check=False)
    except (TimeoutError, subprocess.TimeoutExpired):
        raise ModelFailure("timeout", f"{provider} could not finish within its execution budget.",
                           retryable=True) from None
    except UnicodeError:
        raise ModelFailure("invalid_output", f"{provider} returned invalid text.") from None
    except (OSError, subprocess.SubprocessError):
        raise ModelFailure("adapter", f"{provider} could not start or run.") from None
    if len(child.stdout.encode("utf-8")) > 1_048_576:
        raise ModelFailure("budget_exceeded", f"{provider} response exceeded 1 MiB.")
    if child.returncode != 0:
        failure = _failed_completion(provider, child.stdout + child.stderr)
        if failure is not None:
            raise failure
        raise ModelFailure("adapter", f"{provider} exited with status {child.returncode}.")
    try:
        reported_model = None
        if provider == "codex-cli":
            events = [json.loads(line) for line in child.stdout.splitlines() if line.strip()]
            items = [event["item"] for event in events if event["type"] == "item.completed"]
            if any(item["type"] not in {"agent_message", "reasoning"} and not _blocked_global_instruction_notice(item)
                   for item in items):
                raise ModelFailure("invalid_output", "Codex attempted an external action.")
            content = [item["text"] for item in items if item["type"] == "agent_message"][-1]
            turn = next(event for event in reversed(events) if event["type"] == "turn.completed")
            started = next((event for event in events if event["type"] == "thread.started"), {})
            usage = turn.get("usage", {})
            request_id = started.get("thread_id")
            reported_model = turn.get("model")
        else:
            response = json.loads(child.stdout)
            if response.get("is_error") or response.get("subtype") != "success":
                failure = _failed_completion(provider, child.stdout + child.stderr)
                if failure is not None:
                    raise failure
                raise ModelFailure("invalid_output", "Claude Code did not complete its response.")
            content = response["result"]
            usage = response.get("usage", {})
            request_id = response.get("session_id")
            used_models = list(response.get("modelUsage", {}))
            if len(used_models) == 1:
                reported_model = used_models[0]
        if not isinstance(content, str):
            raise ModelFailure("invalid_output", "Missing CLI worker output.")
        if not isinstance(usage, dict):
            raise ModelFailure("invalid_output", "Invalid CLI usage counters.")
    except (AttributeError, KeyError, IndexError, TypeError, StopIteration, json.JSONDecodeError, RecursionError):
        raise ModelFailure("invalid_output", f"{provider} returned an invalid completion record.") from None
    reported = isinstance(reported_model, str) and bool(reported_model.strip())
    return {
        "model": reported_model if reported else model, "model_id_reported": reported,
        "cli_version": version, "id": request_id,
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": usage.get("input_tokens"), "completion_tokens": usage.get("output_tokens"),
            **{name: usage[name] for name in ("cached_input_tokens", "cost_usd") if name in usage},
        },
    }
