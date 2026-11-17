# Configure research models

The research core sends a versioned model request to the user's chosen adapter. Each reply is a candidate. The deterministic checker separately decides whether the candidate establishes the frozen claim under its approved basis.

## Choose a model for each task

Use the environment prefix `RESEARCH_MULTIPLIER_<ROLE>_`. The roles are `PROVER` for the proof proposal, `AUDITOR` for optional prose review, `EXPLORE` for generating exploratory suggestions, and `LEAN_AUTHOR` for proposing a Lean candidate. Unconfigured `EXPLORE` and `LEAN_AUTHOR` roles inherit the legacy `PROVER` settings. Each role with its own selection uses its own configuration. `PROOF` and `AUDIT` are aliases accepted by the Python configuration method.

Set `PROVIDER` and `MODEL` for a role. Set `FAMILY` when the provider does not identify a known family, such as a local Ollama instance or a compatible endpoint. The built-in providers include Codex CLI, Claude Code, OpenAI, Anthropic, DeepSeek, Ollama, and OpenAI or Anthropic compatible endpoints.

```sh
export RESEARCH_MULTIPLIER_PROVER_PROVIDER=ollama-native
export RESEARCH_MULTIPLIER_PROVER_MODEL=your-installed-model
export RESEARCH_MULTIPLIER_PROVER_FAMILY=your-model-family
export RESEARCH_MULTIPLIER_PROVER_TIMEOUT_SECONDS=90
export RESEARCH_MULTIPLIER_PROVER_MAX_OUTPUT_TOKENS=2048
```

Use `BASE_URL` for a custom endpoint and `BINARY` for a native CLI outside your normal search path. Use the role-specific `API_KEY` for a nondefault remote endpoint. The role's `TIMEOUT_SECONDS` and `MAX_OUTPUT_TOKENS` can narrow the task's own limits.

## Implement a local adapter

A locally installed Python package can implement the [adapter interface](../research_multiplier/models/_pinned/model_adapters.py). The local operator selects the factory by setting `ADAPTER` to `module:factory`, along with a distinct custom `PROVIDER` name, `MODEL` and `FAMILY`.

```python
import json

from research_multiplier.model_adapters import AdapterReply, ModelCapabilities, ModelRequest


class ExampleAdapter:
    capabilities = ModelCapabilities()

    def __init__(self, config):
        self.config = config

    def complete(self, request: ModelRequest) -> AdapterReply:
        # Substitute your local inference call here. Honor the wall-time and
        # token bounds exposed by request["budget"].
        text = json.dumps({"failure_reason": "Example adapter has no model configured"})
        return AdapterReply(text=text, model_id=self.config.model, model_id_reported=False)


def factory(config):
    return ExampleAdapter(config)
```

```sh
export RESEARCH_MULTIPLIER_LEAN_AUTHOR_PROVIDER=example-local
export RESEARCH_MULTIPLIER_LEAN_AUTHOR_MODEL=example-model
export RESEARCH_MULTIPLIER_LEAN_AUTHOR_FAMILY=example-family
export RESEARCH_MULTIPLIER_LEAN_AUTHOR_ADAPTER=example_package.adapters:factory
```

The factory executes as locally installed application code. The request carries the `contract_version`, model ID, messages, parameters and budget. The adapter returns one `AdapterReply` with structured JSON text and the available model identity, revision, request ID, adapter version and usage. Capabilities declare what the adapter supports. For example, a native CLI may report token usage and lack a hard output-token limit. The run records which model identity was reported and the elapsed time. Token counts that the provider omits are left absent.

An adapter can raise `ModelFailure` with a sanitized message and a machine-readable code. Set `retryable=True` for a temporary condition, such as a timeout or rate limit, and include `retry_after_seconds` when available. Task-specific schema mismatches are recorded as invalid candidate responses. Adapter metadata is provenance for review and replay.

## Work with restricted research in the terminal

Use `--confirm-local` when you explicitly want a model to see restricted material
in intake, conjecture discussion and formalization, or Lean search. These routes
use dedicated `RESEARCH_MULTIPLIER_PRIVATE_EXPLORE_` or
`RESEARCH_MULTIPLIER_PRIVATE_LEAN_AUTHOR_` variables. They do not fall back to
the public roles or an active public model profile.

For a trusted Ollama daemon running local model weights, use the native adapter
and an exact numeric loopback URL such as `http://127.0.0.1:11434`.
Run the daemon with cloud inference disabled. This route ignores HTTP proxy
settings, rejects redirects and refuses cloud model identifiers. Its database
record binds the local service, model identity and exact settings. The daemon
is part of the trusted local environment.

An installed adapter can also handle restricted material when its
`module:factory` is in `RESEARCH_MULTIPLIER_TRUSTED_LOCAL_ADAPTERS` and the adapter
declares `ModelCapabilities(private_local=True)`. Approve only code that runs
local inference, avoids external requests and avoids sending telemetry. The
adapter runs as trusted installed Python code with the operator's permissions.

```sh
export RESEARCH_MULTIPLIER_PRIVATE_EXPLORE_PROVIDER=ollama-native
export RESEARCH_MULTIPLIER_PRIVATE_EXPLORE_MODEL=your-installed-model
export RESEARCH_MULTIPLIER_PRIVATE_EXPLORE_FAMILY=its-model-family
export RESEARCH_MULTIPLIER_PRIVATE_LEAN_AUTHOR_PROVIDER=ollama-native
export RESEARCH_MULTIPLIER_PRIVATE_LEAN_AUTHOR_MODEL=your-installed-model
export RESEARCH_MULTIPLIER_PRIVATE_LEAN_AUTHOR_FAMILY=its-model-family
```

Restricted Lean dispatch requires the exact confirmed statement, approved
basis and an explicit researcher authorization tied to the local model
configuration. The proof still goes through Lean and the existing evidence
review. Model calls return candidate text or a structured proof term and
carry the reported model and available usage. A provider-reported USD cost
is recorded separately from any project budget reservation.
