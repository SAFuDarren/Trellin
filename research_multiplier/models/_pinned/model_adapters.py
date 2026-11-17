"""Provider-neutral boundary for generating untrusted research candidates."""

from dataclasses import dataclass, field
from functools import partial
from hashlib import sha256
from importlib import import_module
from importlib.util import find_spec
import json
from math import ceil
import os
import re
from shutil import which
from time import monotonic
from typing import Literal, Protocol, TypedDict
from urllib.parse import urlsplit

from .model_errors import ModelFailure, ModelOutputError
from .model_http import MAX_RESPONSE_BYTES, post_json


PROVIDERS = {
    "codex-cli": ("cli", "codex", "openai", None),
    "claude-code": ("cli", "claude", "claude", None),
    "openai": ("chat", "https://api.openai.com/v1", "openai", "OPENAI_API_KEY"),
    "deepseek": ("chat", "https://api.deepseek.com/v1", "deepseek", "DEEPSEEK_API_KEY"),
    "ollama": ("chat", "http://127.0.0.1:11434/v1", None, None),
    "ollama-native": ("ollama", "http://127.0.0.1:11434", None, None),
    "anthropic": ("messages", "https://api.anthropic.com/v1", "claude", "ANTHROPIC_API_KEY"),
    "openai-compatible": ("chat", None, None, None),
    "anthropic-compatible": ("messages", None, None, None),
}
ROLE_ALIASES = {"PROOF": "PROVER", "AUDIT": "AUDITOR"}
MODEL_ROLES = {"PROVER", "AUDITOR", "EXPLORE", "LEAN_AUTHOR"}


class ModelRequest(TypedDict):
    contract_version: Literal[1]
    model_id: str
    messages: list[dict[str, str]]
    parameters: dict
    budget: dict[str, int]


@dataclass(frozen=True)
class ModelCapabilities:
    output_token_limit_enforced: bool = False
    token_usage_available: bool = False
    private_local: bool = False


@dataclass(frozen=True)
class AdapterReply:
    text: str
    model_id: str
    finish_reason: str = "stop"
    model_revision: str | None = None
    request_id: str | None = None
    adapter_version: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    model_id_reported: bool = True
    cost_usd: str | None = None
    cached_input_tokens: int | None = None


class ModelAdapter(Protocol):
    capabilities: ModelCapabilities

    def complete(self, request: ModelRequest) -> AdapterReply:
        """Run one request within its wall-time budget or raise ModelFailure."""
        ...


@dataclass(frozen=True)
class ModelRun:
    output: dict
    metadata: dict
    transcript: dict


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _configuration(message):
    return ModelFailure("configuration", message)


def _adapter_name(value):
    module, separator, name = value.partition(":")
    if not separator or not name.isidentifier() or not all(piece.isidentifier() for piece in module.split(".")):
        raise _configuration("The model adapter must name an installed module:factory.")
    return module, name


def _local(host):
    return host in {"127.0.0.1", "localhost", "::1"}


def _model_prefix(role, *, private=False):
    requested = role.upper()
    role = ROLE_ALIASES.get(requested, requested)
    if role not in MODEL_ROLES:
        raise _configuration("Choose PROVER, AUDITOR, EXPLORE or LEAN_AUTHOR.")
    if private:
        return f"RESEARCH_MULTIPLIER_PRIVATE_{role}_"
    prefix = f"RESEARCH_MULTIPLIER_{role}_"
    # Older configurations only have a prover and auditor. An explicitly
    # configured new role must be complete on its own.
    selectors = ("PROVIDER", "MODEL", "FAMILY", "ADAPTER", "BASE_URL", "BINARY", "API_KEY",
                 "MAX_OUTPUT_TOKENS", "TIMEOUT_SECONDS", "REASONING_EFFORT")
    if role in {"EXPLORE", "LEAN_AUTHOR"} and not any(os.environ.get(prefix + key) for key in selectors):
        return "RESEARCH_MULTIPLIER_PROVER_"
    return prefix


def require_model_scope(sensitivity, confirm_local):
    """Keep a public request and an explicit restricted local request distinct."""
    if confirm_local and sensitivity != "restricted":
        raise ValueError("--confirm-local requires restricted material.")
    if not confirm_local and sensitivity != "public":
        raise ValueError("A public model request requires confirmed public material. Use --confirm-local with a configured private model.")


def _positive_int_from_env(key):
    value = os.environ.get(key, "")
    if value:
        try:
            result = int(value)
        except ValueError:
            result = 0
        if result <= 0:
            raise _configuration(f"{key} must be a positive integer.")
        return result
    return None


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    model: str
    family: str
    base_url: str
    api_key: str | None = field(default=None, repr=False, compare=False)
    reasoning_effort: str | None = None
    binary: str | None = None
    adapter_factory: str | None = None
    max_output_tokens: int | None = None
    timeout_seconds: int | None = None
    private: bool = False

    def __post_init__(self):
        if not all(_text(value) for value in (self.provider, self.model, self.family)):
            raise _configuration("Configure a provider, model ID, and model family.")
        for value in (self.max_output_tokens, self.timeout_seconds):
            if value is not None and (type(value) is not int or value < 1):
                raise _configuration("Model token and time limits must be positive integers.")
        if self.private:
            self.private_provenance()
        if self.adapter_factory:
            if self.provider in PROVIDERS:
                raise _configuration("Use a distinct provider name for a custom adapter.")
            _adapter_name(self.adapter_factory)
            return
        if self.provider not in PROVIDERS:
            raise _configuration("Choose a supported model provider or configure an adapter.")
        transport, _, canonical_family, _ = PROVIDERS[self.provider]
        if canonical_family and self.family != canonical_family:
            raise _configuration(f"The {self.provider} adapter uses family {canonical_family}.")
        if self.reasoning_effort and transport != "chat" and self.provider != "codex-cli":
            raise _configuration(f"The {self.provider} adapter does not implement reasoning effort.")
        if transport == "cli":
            if not self.binary or not which(self.binary):
                raise _configuration(f"The {self.provider} executable is unavailable.")
            return
        try:
            url = urlsplit(self.base_url)
            port = url.port
        except ValueError:
            raise _configuration("Invalid model API base URL.") from None
        if (
            not url.hostname or url.username or url.password or url.query or url.fragment or port == 0
            or (url.scheme != "https" and not (url.scheme == "http" and _local(url.hostname)))
        ):
            raise _configuration("Model API needs an HTTPS URL or an HTTP loopback URL without credentials.")
        if not _local(url.hostname) and not self.api_key:
            raise _configuration("Configure an API key for the selected remote model endpoint.")

    def private_provenance(self):
        """Return only reviewed transport identity. Never put credentials in a receipt."""
        if not self.private:
            raise _configuration("Choose the explicitly configured private model profile.")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", self.provider) or len(self.model) > 255:
            raise _configuration("Use a bounded provider and model ID for local inference.")
        if self.api_key is not None or self.binary is not None:
            raise _configuration("Private model profiles use local inference without remote credentials.")
        identity = {"provider": self.provider, "model": self.model}
        if self.adapter_factory:
            approved = {name.strip() for name in os.environ.get("RESEARCH_MULTIPLIER_TRUSTED_LOCAL_ADAPTERS", "").split(",")}
            if self.adapter_factory not in approved or len(self.adapter_factory) > 255 or self.base_url:
                raise _configuration("Explicitly approve the installed private-local adapter and omit any base URL.")
            identity.update(transport="installed-local", factory=self.adapter_factory)
        else:
            try:
                url = urlsplit(self.base_url)
                port = url.port
            except ValueError:
                port = None
            if (self.provider != "ollama-native" or port is None or not 1 <= port <= 65_535
                    or url.hostname not in {"127.0.0.1", "::1"}
                    or self.base_url != f"http://{url.netloc}"
                    or url.netloc not in {f"127.0.0.1:{port}", f"[::1]:{port}"}
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,254}", self.model)
                    or re.search(r"(^|[-:/])cloud($|[-:/])", self.model, re.IGNORECASE)):
                raise _configuration("Configure a trusted native Ollama service at a numeric HTTP loopback address "
                                     "and select a local model.")
            identity.update(transport="ollama-loopback", endpoint=self.base_url)
        settings = {
            **identity, "family": self.family, "max_output_tokens": self.max_output_tokens,
            "timeout_seconds": self.timeout_seconds, "reasoning_effort": self.reasoning_effort,
        }
        return {**identity, "config_sha256": sha256(_json(settings).encode("utf-8")).hexdigest()}

    @classmethod
    def from_env(cls, role, *, private=False, use_active=True, config_prefix=None):
        if private and config_prefix is not None:
            raise _configuration("Private inference uses its dedicated role profile.")
        prefix = config_prefix if config_prefix is not None else _model_prefix(role, private=private)
        if not re.fullmatch(r"RESEARCH_MULTIPLIER_[A-Z0-9_]+_", prefix):
            raise _configuration("Choose a named model profile using a RESEARCH_MULTIPLIER_ prefix.")
        provider = os.environ.get(prefix + "PROVIDER", "").strip().lower()
        factory = os.environ.get(prefix + "ADAPTER", "").strip() or None
        if not provider:
            raise _configuration(f"Set {prefix}PROVIDER to a model provider.")
        transport, default_url, default_family, default_key = PROVIDERS.get(provider, (None, None, None, None))
        base_url = os.environ.get(prefix + "BASE_URL", default_url or "").strip().rstrip("/")
        key = os.environ.get(prefix + "API_KEY")
        if not key and default_key and base_url == default_url:
            key = os.environ.get(default_key)
        configured = cls(
            provider=provider,
            model=os.environ.get(prefix + "MODEL", "").strip(),
            family=os.environ.get(prefix + "FAMILY", default_family or "").strip().lower(),
            base_url=base_url,
            api_key=key,
            reasoning_effort=os.environ.get(prefix + "REASONING_EFFORT") or None,
            binary=os.environ.get(prefix + "BINARY", default_url) if transport == "cli" else None,
            adapter_factory=factory,
            max_output_tokens=_positive_int_from_env(prefix + "MAX_OUTPUT_TOKENS"),
            timeout_seconds=_positive_int_from_env(prefix + "TIMEOUT_SECONDS"),
            private=private,
        )
        if use_active and not private and config_prefix is None and role.upper() in {"PROVER", "PROOF"}:
            from . import evolution

            return evolution.select_model_config(role, configured)
        return configured


def _json(value):
    return json.dumps(value, allow_nan=False, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _unique_pairs(pairs):
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("Duplicate JSON key.")
    return value


def _invalid_number(value):
    raise ValueError("Nonfinite JSON constant.")


def _parse(text):
    lines = text.strip().splitlines()
    if len(lines) >= 3 and lines[0] in {"```", "```json"} and lines[-1] == "```":
        text = "\n".join(lines[1:-1])
    output = json.loads(text, object_pairs_hook=_unique_pairs, parse_constant=_invalid_number)
    if not isinstance(output, dict):
        raise ValueError("The response must be a JSON object.")
    # Reject exponent overflow and escaped isolated surrogate characters
    # before a result reaches the content-addressed artifact store.
    _json(output).encode("utf-8")
    return output


def _validate_contract(name, value):
    if find_spec("jsonschema") is not None:
        from .contracts import validate

        validate("model", name, value)


def _reply_field(mapping, key):
    value = mapping.get(key)
    return value if _text(value) else None


def _reply_count(mapping, key):
    value = mapping.get(key)
    return value if type(value) is int and value >= 0 else None


def _reported_cost(value):
    # Only preserve a provider-reported nonnegative fixed-point dollar value.
    if type(value) is int and 0 <= value < 10**12:
        value = str(value)
    return value if isinstance(value, str) and re.fullmatch(r"[0-9]{1,12}(\.[0-9]{1,12})?", value) else None


def _decode_provider_reply(reply, transport):
    try:
        if transport == "messages":
            text = "".join(block["text"] for block in reply["content"] if block["type"] == "text")
            finish = reply["stop_reason"]
            input_field, output_field = "input_tokens", "output_tokens"
        elif transport == "ollama":
            text = reply["message"]["content"]
            finish = reply.get("done_reason") if reply.get("done") else None
            input_field, output_field = "prompt_eval_count", "eval_count"
        else:
            choice = reply["choices"][0]
            text = choice["message"].get("content") or ""
            finish = choice["finish_reason"]
            input_field, output_field = "prompt_tokens", "completion_tokens"
        model = reply["model"]
        if not _text(model) or not isinstance(text, str):
            raise ValueError
    except (KeyError, IndexError, TypeError, AttributeError, ValueError):
        raise ModelFailure("invalid_output", "Model endpoint response does not match its API contract.") from None
    usage = reply if transport == "ollama" else reply.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    return AdapterReply(
        text, model, finish if isinstance(finish, str) else "incomplete",
        model_revision=_reply_field(reply, "model_version"),
        request_id=_reply_field(reply, "id"),
        adapter_version=_reply_field(reply, "cli_version"),
        input_tokens=_reply_count(usage, input_field),
        output_tokens=_reply_count(usage, output_field),
        model_id_reported=reply.get("model_id_reported", True) is True,
        cost_usd=_reported_cost(usage.get("cost_usd")),
        cached_input_tokens=_reply_count(usage, "cached_input_tokens"),
    )


class _CLIAdapter:
    capabilities = ModelCapabilities(token_usage_available=True)

    def __init__(self, config, timeout):
        self.config = config
        self.timeout = timeout

    def complete(self, request):
        from .cli_executor import complete

        controls = ({"reasoning_effort": self.config.reasoning_effort} if self.config.reasoning_effort else {})
        reply = complete(self.config.provider, self.config.binary, request["model_id"], request["messages"],
                         self.timeout, **controls)
        return _decode_provider_reply({"model_id_reported": False, **reply}, "chat")


class _HTTPAdapter:
    capabilities = ModelCapabilities(output_token_limit_enforced=True, token_usage_available=True)

    def __init__(self, config, transport, timeout, post):
        self.config = config
        self.transport = transport
        self.timeout = timeout
        self.post = post

    def complete(self, request):
        config, messages = self.config, request["messages"]
        cap = request["budget"]["max_tokens"]
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.transport == "messages":
            path = "/messages"
            headers["anthropic-version"] = "2023-06-01"
            if config.api_key:
                headers["x-api-key"] = config.api_key
            payload = {"system": messages[0]["content"], "messages": messages[1:], "max_tokens": cap}
        elif self.transport == "ollama":
            path = "/api/chat"
            payload = {"messages": messages, "stream": False, "think": False, "format": "json",
                       "options": {"num_predict": cap, "temperature": 0}}
        else:
            path = "/chat/completions"
            if config.api_key:
                headers["Authorization"] = f"Bearer {config.api_key}"
            token_field = "max_completion_tokens" if config.provider == "openai" else "max_tokens"
            payload = {"messages": messages, token_field: cap, "response_format": {"type": "json_object"}}
            if config.reasoning_effort:
                payload["reasoning_effort"] = config.reasoning_effort
        endpoint = config.base_url.rstrip("/") + path
        reply = self.post(endpoint, {**payload, "model": request["model_id"]}, headers, self.timeout)
        return _decode_provider_reply(reply, self.transport)


def _adapter(config, timeout, post):
    if config.adapter_factory:
        module, name = _adapter_name(config.adapter_factory)
        try:
            factory = getattr(import_module(module), name)
            adapter = factory(config)
        except ModelFailure:
            raise
        except Exception:
            raise _configuration("Could not initialize the configured model adapter.") from None
        if not callable(getattr(adapter, "complete", None)) or not isinstance(
            getattr(adapter, "capabilities", None), ModelCapabilities
        ):
            raise _configuration("The configured adapter does not implement the model adapter contract.")
        if any(type(flag) is not bool for flag in (adapter.capabilities.output_token_limit_enforced,
                                                   adapter.capabilities.token_usage_available,
                                                   adapter.capabilities.private_local)):
            raise _configuration("Model capability flags must be booleans.")
        if config.private and adapter.capabilities.private_local is not True:
            raise _configuration("The approved local adapter must explicitly implement the private-local contract.")
        return adapter
    transport = PROVIDERS[config.provider][0]
    if transport == "cli":
        return _CLIAdapter(config, timeout)
    return _HTTPAdapter(config, transport, timeout, post)


def _validate_reply(reply):
    if (not isinstance(reply, AdapterReply) or not _text(reply.model_id)
            or not isinstance(reply.text, str) or not _text(reply.finish_reason)
            or type(reply.model_id_reported) is not bool):
        raise ModelFailure("invalid_output", "Model adapter returned an invalid reply.")
    for value in (reply.model_revision, reply.request_id, reply.adapter_version):
        if value is not None and not _text(value):
            raise ModelFailure("invalid_output", "Model adapter returned invalid provenance.")
    for value in (reply.input_tokens, reply.output_tokens, reply.cached_input_tokens):
        if value is not None and (type(value) is not int or value < 0):
            raise ModelFailure("invalid_output", "Model adapter returned invalid token counts.")
    if (reply.cached_input_tokens is not None and reply.input_tokens is not None
            and reply.cached_input_tokens > reply.input_tokens):
        raise ModelFailure("invalid_output", "Cached model tokens exceed reported input tokens.")
    if reply.cost_usd is not None and _reported_cost(reply.cost_usd) is None:
        raise ModelFailure("invalid_output", "Model adapter returned an invalid dollar cost.")


def complete_structured(config, role, system, user, max_output_tokens, timeout, *, _post_override=None):
    """Pass a candidate request to one configured adapter and record its provenance."""
    if type(max_output_tokens) is not int or max_output_tokens < 1:
        raise _configuration("The output-token budget must be a positive integer.")
    if type(timeout) not in (int, float) or not 0 < timeout <= 3600:
        raise _configuration("The timeout must be between zero and 3,600 seconds.")
    cap = min(max_output_tokens, config.max_output_tokens or max_output_tokens)
    timeout = min(timeout, config.timeout_seconds or timeout)
    private = config.private_provenance() if config.private else None
    post = partial(post_json, use_proxy=False) if private else post_json
    adapter = _adapter(config, timeout, _post_override or post)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": _json(user)}]
    parameters = {"timeout_seconds": timeout, "requested_model": config.model,
                  "output_token_limit_enforced": adapter.capabilities.output_token_limit_enforced,
                  "token_usage_available": adapter.capabilities.token_usage_available}
    if private:
        parameters["private_model"] = private
    if config.adapter_factory:
        parameters["adapter_factory"] = config.adapter_factory
    if isinstance(adapter, _CLIAdapter):
        parameters["fresh_session"] = True
    if config.reasoning_effort:
        parameters["reasoning_effort"] = config.reasoning_effort
    request = ModelRequest(
        contract_version=1, model_id=config.model, messages=messages, parameters=parameters,
        budget={"wall_seconds": ceil(timeout), "max_attempts": 1, "max_model_calls": 1, "max_tokens": cap},
    )
    _validate_contract("ModelRequest", request)
    from .project_budget import reserve

    reservation = reserve(config, role, request)
    started = monotonic()
    try:
        # Give adapters a separate JSON document so extensions cannot change
        # the research task recorded by the core.
        reply = adapter.complete(json.loads(_json(request)))
    except ModelFailure:
        raise
    except TimeoutError:
        raise ModelFailure("timeout", "Model adapter exceeded the request deadline.", retryable=True) from None
    except Exception:
        raise ModelFailure("adapter", "Model adapter failed to complete the request.") from None
    elapsed = monotonic() - started
    if elapsed > timeout:
        raise ModelFailure("timeout", "Model adapter exceeded the request deadline.", retryable=True)
    _validate_reply(reply)
    if private and (not reply.model_id_reported or reply.model_id.removesuffix(":latest") != config.model.removesuffix(":latest")):
        raise ModelFailure("invalid_output", "The local inference service did not report the requested model identity.")
    try:
        size = len(reply.text.encode("utf-8"))
    except UnicodeError:
        raise ModelFailure("invalid_output", "The model response is not valid UTF-8 text.") from None
    if size > MAX_RESPONSE_BYTES:
        raise ModelFailure("budget_exceeded", "Model response exceeded the 1 MiB limit.")

    usage = {key: value for key, value in (("input_tokens", reply.input_tokens), ("output_tokens", reply.output_tokens))
             if value is not None}
    if reply.cost_usd is not None:
        usage["cost_usd"] = _reported_cost(reply.cost_usd)
    details = {**parameters, "elapsed_ms": round(elapsed * 1000), "model_id_reported": reply.model_id_reported}
    if reply.cached_input_tokens is not None:
        details["cached_input_tokens"] = reply.cached_input_tokens
    if reservation is not None:
        details["project_budget_reservation"] = str(reservation)
    if reply.adapter_version:
        details["adapter_version"] = reply.adapter_version
        if isinstance(adapter, _CLIAdapter):
            details["cli_version"] = reply.adapter_version
    if reply.request_id:
        details["request_id"] = reply.request_id
    metadata = {"provider": config.provider, "id": reply.model_id, "family": config.family, "parameters": details,
                "prompt_sha256": sha256(_json(messages).encode("utf-8")).hexdigest(), **usage}
    if reply.model_revision:
        metadata["revision"] = reply.model_revision
    transcript = {
        "role": role, "model_contract_version": 1, "request": request, "execution": details,
        "response": {"model_id": reply.model_id, "text": reply.text, "finish_reason": reply.finish_reason,
                     "usage": usage},
    }
    if reply.request_id:
        transcript["response"]["request_id"] = reply.request_id
    failed_run = ModelRun({}, metadata, transcript)
    if reply.finish_reason not in {"stop", "end_turn", "stop_sequence"}:
        raise ModelOutputError("Model completion ended before a usable result.", failed_run)
    if adapter.capabilities.output_token_limit_enforced and reply.output_tokens is not None and reply.output_tokens > cap:
        raise ModelOutputError("Model response exceeded the output-token budget.", failed_run, code="budget_exceeded")
    try:
        output = _parse(reply.text)
    except (TypeError, ValueError, RecursionError):
        raise ModelOutputError("Model response is not a single valid JSON object.", failed_run) from None
    response = {"provider": config.provider, "requested_model": config.model, "model_id": reply.model_id,
                "family": config.family, "output": {"structured": output}, "usage": usage}
    if reply.model_revision:
        response["model_revision"] = reply.model_revision
    _validate_contract("ModelResponse", response)
    transcript["normalized_response"] = response
    return ModelRun(output, metadata, transcript)
