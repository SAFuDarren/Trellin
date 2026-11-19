# Interface contracts

English · [简体中文](interfaces.zh-CN.md)

These are the contracts that contributors, plugins, agents and the interface build against. Fixing them before the features exist keeps new features from forcing rework. They implement the [architecture decisions](architecture.md); version and change rules are in [engineering](engineering.md#versioning-and-compatibility).

## Status of each contract

| Contract | Status | Frozen when |
| --- | --- | --- |
| [Common conventions](#1-common-conventions) | **Stable** | Now |
| [CLI conventions](#2-cli-conventions) | **Stable** | Now |
| [Database boundary](#3-database-boundary) | **Stable** | Now |
| [HTTP API v1](#4-http-api-v1) | Draft v1 | Roadmap P1.2 lands |
| [Events v1](#5-events-v1) | **Stable** | P1.3 |
| [Plugin protocol v1](#6-plugin-protocol-v1) | Draft v1 | P2.3 lands |
| [MCP tools](#7-mcp-tools) | Draft | P2.4 lands |
| [Worker protocol v1](#8-worker-protocol-v1) | Draft v1 | P3.3 lands |
| [Python extension surface](#9-python-extension-surface) | Draft | P2.2 lands |

- **Stable**: changes follow the versioning rules: additive changes are allowed; breaking changes need a new version.
- **Draft v1**: the shape is decided and new code follows it. Details may still change until the listed task lands; then it becomes stable.

Machine-readable schemas live in [`research_multiplier/schemas/`](../research_multiplier/schemas/). When a schema exists, it is the source of truth and this document explains it.

| Schema | Contract |
| --- | --- |
| `error.schema.json` | API error body |
| `event.schema.json` | Event envelope |
| `plugin-manifest.schema.json` | Plugin manifest |
| `plugin-request.schema.json` | Plugin request |
| `plugin-response.schema.json` | Plugin response |

---

## 1. Common conventions

These apply to every interface: API, events, plugins, MCP, CLI JSON and exported files.

### Identifiers

- Records are identified by UUIDs written as lowercase canonical strings. Clients treat them as opaque and never derive meaning from them.
- Composite references are objects, not strings to parse. For example, a graph node exposes `kind` and `ref` fields.
- Workspaces, users, organizations, plugins and verifiers each have their own identifier space. A plugin name is a lowercase dotted name such as `org.example.r-verifier`.

### Hashes and canonical JSON

- Hashes are lowercase hexadecimal SHA-256. Field names end in `_sha256`.
- A hash over JSON must name its canonical form:
  - `rm-json-1`: the existing form, used by all records created so far. Sorted keys, two-space indentation, UTF-8, no NaN or infinity (`protocol.serialization.json_text`). Existing records keep it permanently (B7).
  - `jcs`: RFC 8785 JSON Canonicalization Scheme, used by every new contract that crosses a language boundary (plugins, workers, events), because other languages can reproduce it exactly.
- File artifacts are hashed over their raw bytes.

### Time, quantities and money

- Timestamps are RFC 3339 strings. The API, events and plugin messages emit UTC with a `Z` suffix. The database stores `timestamptz`.
- Durations are integers in milliseconds, with names ending in `_ms`.
- Money is stored and computed as integer micro-USD (`_microusd`). A decimal string ending in `_usd` may accompany it for display; never use floating point for money.
- Token counts are integers (`input_tokens`, `output_tokens`).

### Naming and values

- JSON field names are `snake_case` everywhere.
- Enumerations are lowercase `snake_case` strings. Consumers must tolerate values they do not know and show them as unknown, never crash.
- Booleans are true booleans, never `"yes"` or `0`.

### Compatibility rules

- Consumers ignore unknown fields.
- Producers never change the meaning or type of an existing field. A new meaning gets a new field or a new version.
- In responses, optional fields are present with `null` when known to be empty, and absent only when the producer predates the field.

### Text and language

- Text is UTF-8. Research content is stored and returned exactly as entered; it is never translated automatically.
- Machine-facing values (codes, enums, field names) are English identifiers. Human-facing text comes from stable codes that the interface translates (E6).

---

## 2. CLI conventions

- Shape: `research-multiplier [--workspace PATH] <group> <action> [options]`.
- Output streams:
  - Results go to standard output.
  - Diagnostics and errors go to standard error.
  - Commands that support machine output take `--json` and print one JSON document that follows section 1.
- Exit codes:
  - `0`: success.
  - `1`: the command ran correctly but the answer is negative, for example a replay that did not reproduce or a check that failed.
  - `2`: error or invalid usage.
- Commands never prompt when standard input is not a terminal. Destructive or outward-facing actions require an explicit flag or confirmation.
- Configuration variables:
  - New runtime and infrastructure variables use the `RM_` prefix, for example `RM_HUMAN_DSN`.
  - Existing model adapter variables keep the `RESEARCH_MULTIPLIER_` prefix.
  - Secrets are read from the environment or the private credentials file, never from command-line arguments.

---

## 3. Database boundary

- **The database schema is not a public interface.** Tables, views and functions in the kernel schema can change in any release through migrations. Contributors, plugins, agents and the interface use the HTTP API, the CLI, MCP or the Python kernel API, never kernel tables directly.
- A plugin may own a schema of its own for its private data (B6). It reaches kernel records only through the interfaces above.
- Invariants that protect research integrity stay in the database: frozen content, evidence–basis links, recheck propagation and, after P3.1, workspace isolation. Interfaces may add checks but never rely on skipping these.
- Reporting views that outside tools may read can be published later. They will be listed here, versioned, and kept stable like an API.

---

## 4. HTTP API v1

Draft v1. It becomes stable when P1.2 lands. The OpenAPI document generated in P1.2 must follow these rules.

### Base, scope and format

- Base path: `/api/v1`. Requests and responses are JSON (`application/json; charset=utf-8`), except the event stream (section 5).
- Research resources are scoped by workspace in the path: `/api/v1/workspaces/{workspace_id}/…`.
  - Local mode has exactly one workspace, with a real ID, so clients use the same paths in local and server mode (D1, D5).
  - Account resources live outside workspaces: `/api/v1/me`, `/api/v1/organizations/…`.
- Resource names are plural nouns in `kebab-case`: `claims`, `library-items`, `inbox-items`, `progress-policies`, `runtimes`, `jobs`, `threads`, `events`.

### Authentication

- Local mode: the `X-RM-Session` header carrying the launch token (the current behaviour). The server listens on loopback only.
- Server mode:
  - The browser uses an `HttpOnly`, `Secure`, `SameSite=Strict` session cookie.
  - The CLI and workers send `Authorization: Bearer <token>` with personal access tokens.
- Every state-changing request is checked for a matching `Origin` (browser) or a bearer token (CLI and workers).
- Identity always comes from the session or token. Request bodies never name the acting user (B4).

### Methods and actions

- `GET` reads and never changes state.
- `POST` to a collection creates a resource.
- Domain actions are `POST` to a noun sub-resource, for example:
  - `POST …/evidence/{id}/reviews` with `{"decision": "approve" | "return", "reason": "…"}`;
  - `POST …/basis-items/{id}/withdrawals`.
- `PATCH` changes mutable settings only. Frozen research records are never patched; they get new versions or new decisions.
- `DELETE` is used only for things that are truly disposable, such as a draft or a token. Research records are withdrawn or superseded, never deleted.

### Creation and concurrency

- Every creating `POST` accepts an `Idempotency-Key` header (a UUID):
  - repeating the same key with the same body returns the original result;
  - the same key with a different body returns `409 idempotency.conflict`.
- This replaces body fields such as the guidance `request_id`, which remain accepted on the old routes.
- Mutable resources expose a `version` (or `ETag`). Updates send `If-Match`, and a mismatch returns `412`.

### Lists

- Every list returns an object: `{"items": [...], "next_cursor": "…" | null}`. Never a bare array.
- Lists page by cursor: `?limit=` (default 50, maximum 200) and `?cursor=`.
- Filters are query parameters named after fields. Sorting uses `?sort=field,-other` from a documented set per endpoint.

### Long-running operations

Operations that take more than about a second return `202 Accepted` with a job resource: `{"job": {"id": "…", "state": "queued"}}`. Clients follow progress through events or `GET …/jobs/{id}`.

### Errors

The body always follows `error.schema.json`:

```json
{"error": {"code": "review.not_pending", "message": "This evidence is no longer waiting for review.", "request_id": "…", "details": {}}}
```

Error codes are lowercase dotted identifiers (`area.reason`), stable once released. The interface translates by code; `message` is an English fallback. Status codes:

| Status | Meaning |
| --- | --- |
| 400 | Invalid request (`request.invalid`, `request.too_large`) |
| 401 | Not signed in or invalid token |
| 403 | Signed in but not permitted |
| 404 | Not found, including records in another workspace, so their existence is not revealed |
| 409 | State conflict, for example the record changed or is no longer pending |
| 412 | `If-Match` precondition failed |
| 429 | Rate limited |
| 500 | Unexpected error, with no internal details |
| 503 | Database or dependency unavailable; safe to retry |

Every response carries an `X-Request-Id` header, which also appears in errors and server logs.

### Security headers

The current policy remains part of the contract:
- strict CSP with `self` only;
- `Cache-Control: no-store` on API responses;
- `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, frame denial.

---

## 5. Events v1

The envelope is defined in `event.schema.json`.

```json
{
  "id": "1024",
  "type": "evidence.reviewed",
  "schema_version": 1,
  "workspace_id": "…",
  "occurred_at": "2026-11-18T08:00:00Z",
  "actor": {"kind": "user", "id": "…"},
  "subject": {"kind": "evidence", "id": "…"},
  "data": {}
}
```

- `type` is `noun.past_tense_verb`, for example `claim.frozen`, `evidence.recorded`, `evidence.reviewed`, `basis_item.withdrawn`, `job.started`, `job.finished`.
- `actor.kind` is `user`, `agent`, `worker` or `system`.
- Each event type versions its `data` with `schema_version`.
- `id` is a string. It increases within a workspace, and consumers compare ids only within one workspace.
- Delivery is at least once. Consumers deduplicate by `id`. Order is guaranteed only within one workspace.
- Server-sent events: `GET /api/v1/workspaces/{id}/events`. Each message sets `id:` to the event id and `event:` to the type, and `data:` carries the envelope. Clients resume with `Last-Event-ID`.
- `data` carries identifiers and short summaries, never credentials. Large or sensitive content is fetched through the API with the reader's permissions.

Database revision `0076` adds `workspace.changed` for committed writes across
the workspace. Its subject is the workspace and its data contains the affected
area and, when available, a claim ID. Related changes to the same area and
claim in one transaction can share a hint. The reader fetches current state
from the existing API. Existing event types and cursors keep their meaning.

`GET /api/inbox` advertises full coverage as
`workspace_events.coverage = "workspace-writes-v1"`. A workbench that sends
`X-RM-Workbench-Updates: workspace-writes-v1` may also receive the SSE comment
`: workbench-runtime-changed`. It prompts a read of runtime status, including
transitions such as an elapsed lease. Comments carry no cursor.

---

## 6. Plugin protocol v1

Draft v1. It becomes stable when P2.3 lands. Schemas: `plugin-manifest.schema.json`, `plugin-request.schema.json`, `plugin-response.schema.json`.

### Manifest

Every plugin ships `rm-plugin.json` at its root. It declares:

- `protocol_version`, `name`, `version` (semver), `kind` (`verifier`, `domain`, `tool` or `source`), `description` and `license`;
- `entry`: the command to run, as an argument list;
- `environment`:
  - `uv` with a lockfile;
  - `container` with an image digest;
  - `system` for a declared executable such as `lean`;
- `capabilities`: the evidence kinds and domains the plugin handles, and its operations;
- `permissions`: network access, external model calls, and filesystem scope (`workdir` by default);
- `limits`: timeout and memory.

### Invocation

The runner:
1. creates an empty working directory;
2. writes the inputs into it, along with `request.json`;
3. runs the entry command with that directory as the current directory and these variables set:
   - `RM_PLUGIN_REQUEST`: path to the request file;
   - `RM_PLUGIN_RESPONSE`: path where the plugin must write its response;
   - `RM_PLUGIN_WORKDIR`: path to the working directory.

The plugin writes `response.json` and exits `0`. Standard output and standard error are captured as logs and never parsed. A nonzero exit, a timeout, a missing or invalid response, or a permission violation means the run failed and produces no evidence.

### Request and response

- The request carries:
  - `protocol_version`, `request_id` and `operation`;
  - the subject (claim reference and statement);
  - inputs: artifacts with paths and hashes, and basis items with ids, versions, hashes and bodies;
  - parameters and limits.
- The response carries:
  - `protocol_version`, `request_id` and `status` (`completed`, `rejected` or `failed`);
  - for verifiers, a `verdict`: `verified`, `refuted` or `inconclusive`;
  - the evidence kind;
  - output artifacts with hashes;
  - `used_basis`, the basis items actually depended on;
  - coded messages;
  - the plugin's own identity: name, version and `source_sha256`.

### Trust rules

- The kernel validates the response against the schema and recomputes every artifact hash.
- `used_basis` must be a subset of the basis the request supplied.
- A plugin never sets an assurance level. The kernel derives it from the verdict and the plugin's registration (B2).
- Responses use the `jcs` canonical form for any hash over JSON.

---

## 7. MCP tools

Draft. It becomes stable when P2.4 lands.

- The server is `research-multiplier mcp`, over stdio first.
- Tool names are `rm_<area>_<verb>`, for example `rm_claims_get`, `rm_library_search`, `rm_inbox_list`, `rm_verify_run`.
- Read tools have no side effects. Tools that write create proposals only (B2), and they check the workspace's external-data permissions (D4).
- Tool results follow section 1. Failures use the same error codes as the HTTP API.

---

## 8. Worker protocol v1

Draft v1. It becomes stable when P3.3 lands.

- Workers connect outbound over HTTPS with a bearer token. They never accept inbound connections.
- Lifecycle, under `/api/v1/workers/…`:
  1. register with capabilities: Lean version, Mathlib hash, model CLIs, installed plugins and limits;
  2. heartbeat every 15 seconds;
  3. request a lease, which returns a job and a `lease_until` time;
  4. extend the lease while working;
  5. complete with the result and artifact uploads, or release the job.
- An expired lease returns the job to the queue. Each job carries `attempt` numbers, and the same job id with the same attempt is idempotent on completion.
- Wake-up signals may arrive over a server-sent event stream. Polling every 30 seconds is the fallback.

---

## 9. Python extension surface

Draft. It becomes stable when P2.2 lands.

- Public Python API for contributors and Python plugins:
  - `research_multiplier.kernel`, planned in P2.2, for reading and writing kernel records;
  - `research_multiplier.plugin_sdk`, planned in P2.3, for writing plugins in Python.
- Everything else is internal and may change between releases:
  - names starting with `_`;
  - modules under `_pinned/` and `_compat/`;
  - CLI handler modules;
  - SQL.
- Internal modules call kernel functions instead of duplicating queries (A2). Until the kernel module exists, the workflow functions in `research_multiplier.workflows` serve this role.

---

## Changing a contract

1. Open an issue labelled `needs-decision` that describes the change and who it affects.
2. Additive changes to a stable contract can be merged with the schema, the document (both languages) and tests updated together.
3. Breaking changes need a new version (a new API path, `protocol_version` or `schema_version`), a deprecation period as defined in engineering, and a changelog entry.
4. Draft contracts may change in the pull request that implements them. The change must update this document and the schemas.
