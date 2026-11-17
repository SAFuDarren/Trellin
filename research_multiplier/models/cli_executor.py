"""Stateless CLI workers for public proof and audit packets."""

import json
import os
import subprocess
from tempfile import TemporaryDirectory
from time import monotonic

from .model_errors import ModelFailure


def complete(provider, binary, model, messages, timeout, *, reasoning_effort=None):
    if reasoning_effort is not None and provider != "codex-cli":
        raise ModelFailure("configuration", f"The {provider} adapter does not implement reasoning effort.")
    deadline = monotonic() + timeout
    prompt = "\n\n".join(f"{entry['role'].upper()}\n{entry['content']}" for entry in messages)
    if provider == "codex-cli":
        command = [
            binary, "-a", "never", "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules",
            "--skip-git-repo-check", "--sandbox", "read-only", "--disable", "shell_tool",
            "--disable", "memories", "-c", 'mcp_servers.notion={url="http://127.0.0.1:9",enabled=false}',
            "-c", 'web_search="disabled"', "--model", model, "--color", "never", "--json", "-",
        ]
        if reasoning_effort is not None:
            command[4:4] = ["-c", f"model_reasoning_effort={json.dumps(reasoning_effort)}"]
    elif provider == "claude-code":
        command = [
            binary, "-p", "--safe-mode", "--restricted", "--tools", "", "--strict-mcp-config",
            "--mcp-config", '{"mcpServers":{}}', "--disallowedTools", "mcp__*",
            "--no-session-persistence", "--max-turns", "1", "--output-format", "json", "--model", model,
        ]
    else:
        raise ModelFailure("configuration", "Unknown native CLI model provider.")

    # The worker receives only native login context and OS paths, no database DSNs or API keys.
    inherited = {"HOME", "PATH", "USER", "LOGNAME", "TMPDIR", "SYSTEMROOT", "APPDATA", "LOCALAPPDATA",
                 "USERPROFILE", "LANG", "LC_ALL", "CODEX_HOME", "CLAUDE_CONFIG_DIR"}
    environment = {key: value for key, value in os.environ.items() if key in inherited}
    try:
        with TemporaryDirectory(prefix="rm-proof-worker-") as directory:
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
        signal = (child.stdout + child.stderr).casefold()
        if any(word in signal for word in ("not logged in", "please run /login", "authenticate", "unauthorized")):
            raise ModelFailure("authentication", f"{provider} is not signed in. Complete login using the native CLI.")
        if any(phrase in signal for phrase in ("rate limit", "too many requests")):
            raise ModelFailure("rate_limit", f"{provider} reached a rate limit.", retryable=True)
        raise ModelFailure("adapter", f"{provider} exited with status {child.returncode}.")
    try:
        reported_model = None
        if provider == "codex-cli":
            events = [json.loads(line) for line in child.stdout.splitlines() if line.strip()]
            items = [event["item"] for event in events if event["type"] == "item.completed"]
            if any(item["type"] not in {"agent_message", "reasoning"} for item in items):
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
