# Roadmap and acceptance criteria

English · [简体中文](roadmap.zh-CN.md)

This roadmap turns the [architecture decisions](architecture.md) into phases. Each task names its owner track, the decisions it implements, and the acceptance criteria that must hold before it counts as done. A phase is complete when every task in it meets its criteria and the phase exit criteria hold.

Owner tracks:

- **Backend**: Python, SQL and server work, done by the maintainer.
- **Front end**: the React interface, done with Claude.
- **Maintainer**: project and community decisions.

Each task becomes a GitHub issue in the milestone of its phase. The issue copies the acceptance criteria and cites decision numbers. See [engineering and project management](engineering.md).

Tasks within a phase may run in parallel unless a dependency is listed.

---

## Publication gate

The repository stays private until all of these hold; then it becomes public (E7) and branch protection is switched on (P0.4).

- P0 is complete: tests in the repository (P0.2), tooling (P0.3), community basics including milestones and labels (P0.4), dependency scanning (P0.5) and the regression and handover stress tests (P0.6).
- The React interface reaches parity (P1.4), and the function rail with its section pages is built (P1.5).
- The 1.0.0 release is rebuilt from the current code under Apache-2.0, and the old MIT draft release is deleted.
- An engineering audit is run just before publication, and it records no open P0 finding.

---

## P0 Stabilize (now)

Goal: a clean, tested baseline to build on.

### P0.1 Commit the current work (Backend)

Acceptance criteria:

- The working tree is clean. Changes land as separate commits:
  1. the agent guide;
  2. the workbench backend (proof graph, activity, guidance, migrations 0069–0071);
  3. the workbench front end;
  4. the section endpoints with the inbox and budget refactors;
  5. the architecture, roadmap and engineering documents.
- Migrations 0069–0071 apply both to an empty database and as an upgrade from 0068.
- The installed-package smoke check in CI passes for every commit.
- `research-multiplier review inbox` returns the same rows as before the refactor on the same database.

### P0.2 Tests in the repository and CI (Backend; E4)

Acceptance criteria:

- `tests/` exists with pytest markers for at least `offline`, `postgres` and `lean` lanes.
- `uv run pytest -m offline` needs no database or Lean and finishes in under five minutes.
- The `postgres` lane runs in CI against a disposable PostgreSQL service with pgvector on every pull request. The `lean` lane runs on a schedule and before releases.
- The built wheel contains no test files. CI checks this by listing the wheel.
- The cases from the external validation bundle are ported, or wrapped with runners and assertions in the repository. Only large fixed data may stay outside, fetched by hash.
- The workbench server has tests for:
  - session token and Host and Origin checks;
  - CSP and security headers;
  - every `/api` route on a fixture database;
  - request validation for `POST /api/review` and `POST /api/guidance`.

### P0.3 Development tooling baseline (Backend)

Acceptance criteria:

- `uv sync` creates the development environment from a committed `uv.lock`.
- CI runs `ruff check` and `ruff format --check`. Existing violations may be listed in a baseline that only shrinks.
- CI runs `pyright` with no errors: basic mode for the package, strict mode for new kernel modules.
- Dependabot, or an equivalent, is enabled for Python, GitHub Actions and (after P1.4) npm dependencies.

### P0.4 Community basics (Maintainer)

Acceptance criteria:

- DCO sign-off (E9) is enforced in CI, and the license is reviewed once more, before the first outside pull request is merged.
- Present in the repository: `CODE_OF_CONDUCT.md`, `SECURITY.md` (private reporting channel), issue and pull request templates, and `CODEOWNERS`.
- `CONTRIBUTING.md` explains the change checklist and points to the architecture decisions.
- The GitHub Project board and milestones P0–P6 exist, with labels as defined in [engineering](engineering.md#issues-and-planning).
- `main` is protected: Checks, Tests and DCO must pass and a code owner must approve before merging. This needs the repository to be public, which follows the publication gate (audit finding A-202611-01).

### P0.5 Dependency and vulnerability scanning (Backend)

Acceptance criteria:

- `.github/dependabot.yml` covers pip, GitHub Actions and (after P1.4) npm.
- CI runs `pip-audit` on the installed package with its extras. A known vulnerability fails the check unless an ignore entry with a reason and an expiry date covers it.
- GitHub Actions in workflows are pinned to commit SHAs, and Dependabot updates them.

### P0.6 Regression and handover stress tests (Maintainer)

Acceptance criteria:

- Stress test 4 from the [engineering audit](audits/README.md): each injected fault in the maintainer's audit plan makes an offline test fail. Any fault that slips through gets a new test.
- Stress test 6: a fresh agent with only the repository completes an ordinary task, following `AGENTS.md` without extra explanation. Every rule it missed is fixed in `AGENTS.md` or enforced in CI.
- Both results are recorded in the next audit (audit finding A-202611-13).

Exit criteria for P0: a new contributor can clone, run `uv sync` and `uv run pytest -m offline`, open a pull request, and see CI run every required check.

---

## P1 Foundations

Goal: identity, a contracted API, live updates, and the new React interface with the rail sections.

### P1.1 Local user identity (Backend; B4)

Acceptance criteria:

- A user table exists. `init-workspace` (or `bind-workspace` for existing projects) creates one local user and records its ID in the workspace configuration.
- New write functions take the user ID. Existing write functions accept it and keep `actor_ref` only as a display name.
- The workbench session resolves to the local user. `POST /api/review` no longer accepts an `actor` field.
- Tests show that a write without a resolved user fails, and that the recorded user matches the session.

### P1.2 API contract (Backend; D1)

Acceptance criteria:

- All workbench routes are served under `/api/v1`. The previous `/api` routes remain until the React interface switches over (P1.4), then are removed.
- An OpenAPI 3.1 document lives in the repository and is either the source of truth or generated from code in CI.
- A test asserts that every served route appears in the document and that responses on the fixture database validate against it.
- Errors use one body shape, `{"error": {"code": "...", "message": "..."}}`. Codes are stable identifiers the interface translates; messages are English fallbacks.
- `pnpm gen:api` regenerates the TypeScript types, and CI fails if the generated files differ from the committed ones.
- Every response carries `X-Request-Id`. Server logs are structured (one JSON object per line) with the request ID, route, status and duration, and never research content (audit finding A-202611-10).

### P1.3 Event log and live updates (Backend; B5, D3)

Acceptance criteria:

- An events table records workspace, user, event type, subject and summary. Every write workflow appends its event in the same transaction. A test enumerates the write functions and checks each one.
- Inserting an event issues `NOTIFY`. `GET /api/v1/events` streams server-sent events and resumes from `Last-Event-ID`.
- On a local machine, an event reaches a connected client in under one second.
- Event summaries contain no credentials or connection strings. Research text appears only where the subject itself is shown to the same workspace.

### P1.4 React interface with parity (Front end; E1, E6)

Depends on P1.2 for generated types (it may start against the current routes).

Acceptance criteria:

- `web/` is a pnpm package (React, TypeScript, Vite). `pnpm build` writes into the Python package's assets, and the Python package ships the built files, so users need no Node.
- The server's CSP stays unchanged: no inline scripts or styles, and no external hosts.
- Parity with the current workbench:
  - the claim list and search;
  - the dependency graph with pan, zoom and fit;
  - the global atlas, movable and resizable;
  - draggable splitters with persisted sizes;
  - the review queue and node details;
  - the assumption impact preview;
  - the model conversation with the two-step Codex confirmation;
  - light and dark themes.
- Every user-facing string comes from message catalogs: English default, Chinese available. A lint rule rejects literal strings in JSX.
- Vitest covers logic. Playwright covers the parity list against the fixture server in CI.
- The initial JavaScript bundle stays under 300 KB gzip.

### P1.5 Rail and section pages (Front end)

Depends on P1.4. It uses the section endpoints from P0.1, moved under `/api/v1` in P1.2.

Acceptance criteria:

- The function rail and the review, claims, library, conversations, team (runtimes, agents, usage, job queue) and progress pages match the approved mockups.
- Approving and returning evidence works end to end on a test project, and the review count on the rail updates.
- Every page has loading, empty and error states, works by keyboard alone, and passes axe checks with no serious violations.
- Pages show only data from the API. Nothing is invented on the client, for example usage limits that the backend does not record.

### P1.6 Live interface (Front end; D3)

Depends on P1.3 and P1.4.

Acceptance criteria:

- The interface subscribes to `/api/v1/events` and refreshes affected views. Polling remains only as a fallback while disconnected.
- A change made from the CLI appears in an open workbench within two seconds.

### P1.7 Architecture map and enforced layering (Backend; A1, A2, A3)

Acceptance criteria:

- A generated dependency graph of the current packages is published in `docs/` and regenerated by a script, so it never drifts from the code.
- import-linter (or an equivalent) runs in CI with contracts:
  - `cli` and `workbench` may import `workflows`, but not each other's internals;
  - `workflows` do not import `cli`;
  - no new kernel code imports Lean-specific modules.
  Existing violations are listed and may only shrink.
- The audit can report the change impact radius of recent PRs from this graph (audit finding A-202611-05).

### P1.8 Release recovery rehearsal (Maintainer)

Acceptance criteria:

- On a disposable project, install the previous release, back up, upgrade to the candidate, then restore the backup into a new database with the previous release. Both steps succeed and the records match.
- The steps are added to [release preparation](release.md), and the rehearsal result is recorded with each release (audit finding A-202611-09).

Exit criteria for P1: a single researcher uses the React workbench for daily work. Every write is attributed to a user, and every API response is covered by the contract.

---

## P2 Kernel and plugins

Goal: separate the domain-independent kernel from Lean and open the plugin boundary.

### P2.1 Type registry (Backend; B3)

Acceptance criteria:

- Registry tables exist for evidence kinds, verifiers, domains and basis item kinds, each with a schema version. Existing kinds are registered by migration.
- A CI lint rejects new `kind IN (...)` lists and new `'lean-core'` literals in migrations after the cutoff revision. Existing occurrences are allow-listed.
- All regression lanes pass unchanged.

### P2.2 Kernel and Lean boundary (Backend; A3)

Acceptance criteria:

- A design note (added to the architecture document) classifies every current table, view and function as kernel or Lean plugin.
- New Lean-specific objects live in a separate schema. The kernel evidence interface no longer requires Lean fields.
- The proof graph API returns a generic claim–evidence graph, with the Lean structure as an optional view.

### P2.3 Plugin protocol and runner (Backend; C1–C3, B7)

Acceptance criteria:

- The plugin manifest and the request and response formats are published as versioned JSON Schemas.
- `research-multiplier plugin list | install | check` manage plugins. Each plugin runs in its own environment (a uv environment or a container) with a time limit, a memory limit, and no network unless the manifest declares it.
- Evidence produced by a plugin records its name, version and source fingerprint.
- Three reference plugins pass the same conformance suite: the Lean verifier wrapped as a plugin, a Python example, and a non-Python example (for example R or a compiled program).

### P2.4 MCP server (Backend; C4, D4)

Acceptance criteria:

- `research-multiplier mcp` serves over stdio. It exposes tools for reading claims, the library, the graph and the inbox, and for running verifiers.
- Results enter only as proposals (B2). Calls respect the workspace's external-data permissions (D4).
- CI connects an MCP client to the server and calls every tool.

### P2.5 Assurance levels (Backend and Front end; B2)

Depends on Q5 being decided.

Acceptance criteria:

- Each claim exposes an assurance level computed from its evidence and reviews, never stored as a free value.
- Withdrawing a basis item lowers the level of dependent claims, with a test.
- The interface shows the level and the reason for it.

Exit criteria for P2: a verifier written outside this repository, in a language other than Python, produces evidence that the kernel records and the interface displays, with no kernel code changes.

---

## P3 Group server

Goal: one deployment shared by a research group (D5, D6).

### P3.1 Workspace isolation (Backend; D5)

Acceptance criteria:

- Every workspace-owned table has a non-null, indexed workspace ID. Row-level security is enabled and forced.
- The session sets the workspace with `SET LOCAL`.
- For every table, an isolation test shows that reads from another workspace return no rows and writes fail.
- An import tool moves a single-project database into a shared database and verifies row counts and content hashes.
- Query plans for the main pages remain index-based at 100 workspaces of test data.

### P3.2 Accounts, sign-in and roles (Backend; B4, D6)

Acceptance criteria:

- The data model has users, organizations, workspaces and memberships, with roles owner, admin, member and viewer. A permission matrix is documented and tested.
- Sign-in works by email code and by OIDC.
- Personal access tokens for the CLI and workers are scoped, expiring and revocable, and stored only as hashes.
- Sign-in, token creation and role changes produce events.

### P3.3 Worker protocol (Backend; D2)

Acceptance criteria:

- `research-multiplier worker connect --server URL` authenticates with a token and connects outbound over HTTPS only. It sends a heartbeat every 15 seconds.
- Each worker advertises its capabilities: Lean version, Mathlib hash, model CLIs and installed plugins. The server assigns only jobs the worker can run.
- A killed worker's job is released when its lease expires and is retried elsewhere. A test runs two workers and kills one mid-job.
- The team pages show each runtime's status, capabilities and current load.

### P3.4 External-data permissions (Backend; D4)

Acceptance criteria:

- Permissions are configurable per workspace, claim and person. Every outgoing call checks them and records who granted access.
- A test shows that a denied call is never dispatched.
- Telemetry, if ever added, is off unless the administrator turns it on.

### P3.5 Deployment (Backend; E3)

Acceptance criteria:

- On a clean Linux host, `docker compose up` brings up PostgreSQL with pgvector, the API and a worker with Lean and the Mathlib cache. Health checks pass.
- Documented commands cover backup, restore and upgrade. An upgrade runs the migrations, and a test upgrades from the previous release.
- The guide states hardware needs and TLS setup.

### P3.6 Collaboration interface (Front end)

Acceptance criteria:

- The interface has pages for sign-in, members and roles, runtimes, tokens and permissions.
- Each action is hidden or disabled according to the user's role, and the server also enforces the role.

Exit criteria for P3: three people in one group share a server, each signed in. Two members' computers run as workers. A review by one member appears live for the others, and no workspace can read another's data.

---

## P4 Second use case

Goal: prove that the kernel is domain-independent (Q6; the suggested case is reproducible computational experiments).

Acceptance criteria:

- A reproducible-run plugin records the code version, data hashes and environment, and produces evidence.
- A rerun on a different worker reproduces the recorded outputs, and the evidence is accepted.
- Changing a dataset's version marks dependent claims for recheck.
- The kernel needs no Lean-specific change. Any kernel change this phase requires is recorded as a design note and reviewed against A1–B7.

---

## P5 AI system

Goal: agents carry out research tasks under the kernel's rules.

Acceptance criteria:

- Tasks can be assigned to people or agents. Agent runs record model, prompt version, inputs, cost and outputs, and every output enters as a proposal.
- Reusable research playbooks (skills) are versioned and attached to tasks.
- Progress policies generalize to scheduled and triggered automation across task types.
- Per-workspace cost limits stop dispatch when reached.
- The existing evaluation harness compares strategies on recorded tasks.

---

## P6 Product and distribution

Goal: release readiness beyond self-hosting.

Acceptance criteria:

- A desktop app bundles the Python runtime and the built interface, and registers the local machine as a runtime.
  The [Trellin macOS daily-use build](desktop.md) tracks local Launchpad, workspace selection and graph resume in [#44](https://github.com/SAFuDarren/Trellin/issues/44).
- Readiness for a hosted service: any paid extensions are separated from the Apache-2.0 core (E9); metering and multi-organization limits work; the security review is completed.
- A documentation site is published in English and Chinese.
- Releases are automated, with signed artifacts and published SHA-256 sums.
