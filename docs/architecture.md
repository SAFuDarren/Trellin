# Architecture direction and design decisions

English · [简体中文](architecture.zh-CN.md)

Research Multiplier's long-term goal is a **research collaboration framework and research AI system**: researchers and AI agents in one group share a workspace where they pose questions, produce evidence and review each other's work, and always know which conclusions are trustworthy and why. Formal Lean proof is the first and most strongly verified use case, not the only one.

This document records the settled direction and the questions still waiting for a decision. The [roadmap](roadmap.md) turns it into phased tasks with acceptance criteria, and [engineering and project management](engineering.md) describes how the work is done. New code follows the settled decisions. If a change depends on an open question, ask before building on it. Each decision has a number that commit messages and reviews can cite, for example "per A3".

Status: **settled** means follow it; **open** means a decision is still needed, so do not hard-code an answer.

---

## A. Layers and boundaries

### A1 Five layers (settled)

| Layer | Responsibility | Examples |
| --- | --- | --- |
| Research kernel | Domain-independent research records and rules | Claims, basis, evidence, review, impact analysis, tasks, identity, events |
| Plugins | How one domain or one kind of verification works | Lean verifier, flow certificate checker, reproducible runs, literature sources |
| Collaboration | Several people and machines working together | Workspaces, members and roles, task assignment, inbox, runtime registration |
| AI system | Agents carrying out research tasks | Literature surveys, hypotheses, proof attempts, data analysis, reusable research playbooks |
| Entry points | How people and agents use the system | CLI, HTTP API, web workbench, MCP server |

Dependencies point downward only. Plugins depend on kernel interfaces, the kernel imports no plugin, and entry points call only kernel and plugin functions.

### A2 Business logic lives in one place (settled)

CLI handlers, HTTP handlers and the web front end only parse input and present results. Logic that reads or writes research records belongs in functions under `workflows/` (and later the kernel modules), and the CLI and the API call the same function. For example, the review inbox query is `research_review.inbox()`; both `review inbox` and the workbench's `/api/inbox` call it.

### A3 Lean is a plugin, not the kernel (settled; migrate in steps)

New code must not treat Lean as the only case:

- Kernel tables, functions and interfaces do not carry names such as `lean` or `lean-core`.
- Lean-specific concepts (Lean statements, proof obligations, reductions, the Mathlib version) belong to the Lean plugin.
- In the interface, the Lean proof structure is one view of the dependency graph; the general view is the claim–evidence graph.

Today the SQL migrations contain 28 hard-coded `'lean-core'` literals, and many `lean_*` tables and functions sit directly in the `rm` schema. They need not change at once, but new features must not deepen this coupling, and code touched for other reasons moves toward the plugin boundary.

---

## B. Research kernel

### B1 Kernel concepts (settled)

| Concept | Meaning |
| --- | --- |
| Research question | What a researcher wants to answer; it may start vague and be clarified |
| Claim | A frozen, exact statement and scope. Its content never changes after freezing; a revision creates a new version |
| Basis | Versioned items a claim depends on: assumptions, definitions, established results, datasets, publications |
| Artifact | Proof files, code, data, notebooks, papers, stored by content hash |
| Evidence | One verification's or review's judgment on a claim, recording the basis versions, artifact hashes and verifier version it used |
| Verifier | The program or procedure that produces evidence, with its own identity, version and source fingerprint |
| Review | A decision with a reason, made by an identified researcher on evidence or a claim |
| Impact analysis | When a basis item is withdrawn or revised, marking the evidence and claims that used it for recheck |
| Task | A unit of work assigned to a person or an AI, such as "attempt a proof", "reproduce this experiment" or "review this evidence" |
| Event | An immutable record of each state change (see B5) |

The current implementation already has most of these: frozen claims, versioned formal items, evidence and review, `verification_basis_use` and recheck propagation. Generalizing mainly means separating them from Lean, not rewriting them.

### B2 Assurance levels (principle settled; levels open)

Every conclusion shows how far it can currently be trusted. This is the essential difference between this product and general agent collaboration tools. Principles:

- A **verifier or a review** sets the assurance level. The producer of a result cannot claim it.
- **Every AI output enters as a proposal** at the lowest level. Only a verifier check or a human review can raise it. An AI cannot approve or upgrade any conclusion, including its own.
- When a basis item is withdrawn, assurance drops automatically through impact analysis.

Draft levels, strongest first: formal proof; deterministic check (certificates, counterexamples); reproducible (code, data and environment rerun to the same result); human review; cited source; AI-generated only. The exact levels and their order are open question Q5.

### B3 Types are registered, not hard-coded in the database (settled)

Evidence kinds, domains, verifiers and basis item kinds are added by registration, with a schema version. Do not add domain-specific values to SQL `CHECK (kind IN (...))` lists; 20 SQL files currently contain such lists, and they will be consolidated over time. The database enforces only general invariants, for example "every piece of evidence references a registered verifier version and the basis versions it used".

### B4 Identity (settled)

Every write records an **authenticated user ID**. In local mode, initialization creates a local user and every action is recorded under that user; in multi-user mode the ID comes from sign-in. Today's free-text `actor_ref` (about 220 occurrences in SQL) remains a display name and no longer serves as identity. New write endpoints take identity from the session and never accept a caller-supplied name.

### B5 Event log (settled)

Every state change appends an event: workspace, actor, event type, subject, summary and time. Live updates, audit, multi-device sync and notifications derive from the event log instead of keeping their own records. Existing ledgers stay and are gradually unified under this mechanism.

### B6 PostgreSQL is the single system of record (settled)

Research records are authoritative only in PostgreSQL, and database constraints and functions continue to enforce key invariants; this is the basis of auditability. Plugin tables live in the plugin's own schema, kernel tables in the kernel schema. File artifacts are stored by content hash in the workspace or object storage, and the database keeps only references and hashes.

### B7 Verifiers are reproducible (settled)

A verifier must be exactly reproducible: record its identity, version and source fingerprint. This is why `_pinned/` exists: it holds the verifier, adapter and historical evaluation sources behind recorded fingerprints, and `_compat/` supplies their original import paths. New verifiers follow the same principle, and the source of a verifier that has already produced evidence must not be changed in place.

---

## C. Plugins

### C1 Plugins run as separate processes (settled)

The system starts a plugin process and exchanges input and output as JSON files in a working directory, with standard output and error kept as logs (the exact protocol is in [interface contracts](interfaces.md#6-plugin-protocol-v1)). Plugins can therefore be written in any language: Lean, R, Julia, MATLAB, C++ and Fortran all fit, and the current Lean invocation already works this way. The Python SDK is a convenience for Python plugin authors, not a requirement.

### C2 Plugin manifest (settled; format to be detailed)

Each plugin ships a manifest: name, version, type (verifier, domain package, tool, source), entry command, environment requirements, input and output schemas, and the permissions it needs, such as network access or external model calls.

### C3 One environment per plugin (settled)

Dependencies differ widely between disciplines, so plugins cannot share one Python environment. Each plugin runs in its own environment, created separately by uv or provided by a container.

### C4 MCP server (settled)

Verifiers and research tools are exposed over MCP so external agents such as Claude Code or Codex can call them directly. Results produced through MCP also enter as proposals under B2.

---

## D. Collaboration and execution

### D1 One API for local and cloud use (settled)

The local workbench and the group server use the same HTTP API, defined by an OpenAPI contract that follows the [interface contracts](interfaces.md). Front-end types are generated from the contract. Local mode is the same service with a single user.

### D2 Long-running work goes through the queue to workers (settled)

Lean verification, model calls, experiment runs and other slow work enter the database queue as tasks for workers to claim. A worker runs as a long-lived process, connects out to the server, authenticates with a token and sends regular heartbeats; group members' computers can join as runtimes. In local mode the worker runs on the same machine and the flow is unchanged.

### D3 Live updates derive from events (settled)

Start with PostgreSQL LISTEN/NOTIFY and server-sent events (SSE). Introduce WebSocket only when two-way real-time traffic is needed.

### D4 Data leaving a workspace requires authorization (settled)

Any operation that sends research content to an outside service (an external model or cloud tool) needs prior authorization and records who granted it. Today's "confirm before sending to Codex" rule generalizes into permissions configured per workspace, claim and person.

### D5 Workspaces share one database, isolated by row-level security (settled)

A deployment uses one PostgreSQL database. Every row owned by a workspace carries a workspace ID, and row-level security policies keep one workspace from seeing another's data; kernel functions and views run within the session's workspace. Local mode uses the same structure with a single workspace.

Today each project binds its own database (`bind-workspace`). The migration adds workspace IDs and row-level security policies to kernel tables and provides a tool to import an existing single-project database into a shared one. Single-project databases keep working until then.

### D6 Self-hosting first, room for a hosted service later (settled)

The current target is a research group deploying on its own server. The design must not assume that one deployment serves one organization: identity, workspaces, permissions and metering are modeled so they extend to several organizations, so that a hosted service run by us later needs no data model change.

---

## E. Technology choices

### E1 Languages (settled)

- Kernel, orchestration and plugin SDK: Python. The scientific ecosystem is concentrated in Python, which also makes plugins easiest for researchers to write.
- Front end: React with TypeScript, built with Vite, in `web/`. The compiled files ship inside the Python package, so users do not need Node.
- Plugins: any language (C1).
- Go or other languages only for separate components that genuinely need them later (for example a high-concurrency real-time gateway), never to rewrite the core.

### E2 Python engineering rules (settled)

- Strict type checking (pyright or mypy) for kernel code.
- External data structures are defined by schemas (JSON Schema or OpenAPI), not by convention.
- Heavy pure-Python loops move to numpy, an existing solver, or an external plugin.
- The API server uses async IO; workers use multiple processes.

### E3 Installation and distribution (settled)

- The base install keeps zero dependencies.
- Individuals: `uv tool install research-multiplier`.
- Group servers: Docker Compose with PostgreSQL (with pgvector), the API service, and a worker with Lean and a Mathlib cache preinstalled.
- Desktop app (later): bundles a Python runtime so users never see Python.

### E4 Tests live in the repository and run in CI (settled)

Tests sit in the same repository as the code and run in CI on every pull request; the installed package excludes them. The external per-version regression suite moves into the repository over time. Large fixed data or Lean projects may stay stored separately, but their runners and assertions live in the repository.

### E5 Interface vocabulary (settled)

Internal concepts serve agents and may be precise and dense. The interface exposes only a few concepts and tells users what the situation is and what they need to do.

### E6 Interface languages (settled)

The interface is English by default with a complete Chinese translation. Every user-facing string lives in message catalogs from the React migration onward, and API errors carry stable codes that the interface translates. Research content is stored as entered and never translated automatically.

### E7 Open community, tracked on GitHub (settled)

The project is developed as an open community project in a public repository. The repository becomes public once the [publication gate](roadmap.md#publication-gate) in the roadmap is met. Tasks, bugs and milestones live in GitHub Issues and a GitHub Project board, with milestones matching the roadmap phases. Contribution rules are in [engineering](engineering.md#open-community).

### E8 Versioning starts at 1.0.0 (settled)

The first public release is 1.0.0, built from the current code under the Apache License 2.0; the earlier private 1.0.0 draft is never published. Semantic versioning continues from it. Each PR merged into main normally advances the patch version. A maintainer-approved feature milestone advances the minor version and resets the patch. An approved major milestone that opens the next generation, or a breaking package change, advances the major version and resets the minor and patch. Choose the package version against the latest main. Tags and release publication are separate actions. The HTTP API, plugin protocol and event payloads carry their own versions, and new interfaces may be marked experimental. See [engineering](engineering.md#versioning-and-compatibility).

### E9 License and contributions (settled)

Goal: people and institutions can adopt and contribute without hesitation, while a later product remains possible.

- **Code license: Apache License 2.0** for every release, starting with 1.0.0. It is as permissive as MIT, adds an explicit patent grant and patent retaliation that companies and universities look for, and matches Lean 4 and Mathlib. `LICENSE` holds the unmodified license text; the copyright notice is in `NOTICE`. Pre-release commits in the history that predate the switch carry an MIT License file; they were never released, and `NOTICE` says so.
- **Contributions: Developer Certificate of Origin (DCO).** Every commit carries `Signed-off-by` (`git commit -s`); contributions come in under the same Apache-2.0 terms they go out under (inbound = outbound, Apache-2.0 section 5). No separate agreement to sign.
- **Name and logo:** not covered by the code license (Apache-2.0 section 6). `TRADEMARKS.md` lets anyone describe their work as using or extending Research Multiplier and asks forks and hosted services to use a different name. This protects the brand without restricting the code.
- **Documentation** follows the code license. **Users' research data, proofs and results** belong to them and are not covered by the software license. **Plugins** may choose their own licenses and declare them in the manifest.
- **If a product follows:** paid features ship as separate extensions under their own license (open core), in a clearly separated directory or repository; the Apache-2.0 core never depends on them and needs no relicensing.
- **Changing course later:** a different license for future versions, such as AGPL with dual licensing or a source-available license, applies only to code whose copyright holders agree. Until the first outside contribution is merged that is only the maintainer; afterwards every contributor whose code remains must agree, or their code must be rewritten. Review the choice once more before merging the first outside pull request.

This is a project decision, not legal advice; have a lawyer review license and trademark texts before relying on them.

---

## F. Migration order

The [roadmap](roadmap.md) holds the detailed tasks and acceptance criteria; this list is the summary.

1. Organize and commit the current changes (agent guide, workbench, migrations 0069–0071, section endpoints); move the tests into the repository and run them in CI (E4).
2. Introduce local user identity (B4); new write endpoints take identity from the session.
3. Define the API contract, move the front end to TypeScript, and finish the rail section pages.
4. Design the boundary between the kernel and the Lean plugin (A3, B3), starting with the parts new features touch.
5. Implement the event log and SSE (B5, D3).
6. Add workspace IDs and row-level security (D5), the worker registration protocol, and the group server deployment (D2, E3).
7. Test the kernel abstractions against a second use case (Q6).

---

## G. Open questions

Decided: Q1 → D5, Q2 → D6, Q3 → E9, Q4 → E4, Q7 → E8 (re-decided after [audit 2026-11](audits/2026-11.md), finding A-202611-04), Q8 → E9, Q9 → E7 (public after the roadmap's publication gate).

| No. | Question | Affects |
| --- | --- | --- |
| Q5 | The exact assurance levels and their order, for example whether human review or reproduction ranks higher | Interface display, upgrade rules |
| Q6 | The second use case; the suggestion is reproducible computational experiments | Testing the kernel abstractions |
