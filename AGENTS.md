# Developing Research Multiplier

Instructions for coding agents and contributors working on this repository. (The guide that research workspaces receive as their own `AGENTS.md` is a different file: [`research_multiplier/agent_guide.md`](research_multiplier/agent_guide.md).)

Research Multiplier is becoming a research collaboration framework and research AI system. Lean proof checking is its first plugin; the kernel must stay domain-independent.

## Read before changing code

| Document | Use it for |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | Numbered decisions (A1–E9). Settled decisions are binding; cite their numbers in commits and PRs. |
| [docs/interfaces.md](docs/interfaces.md) | Contracts others build against: conventions, CLI, database boundary, API, events, plugins, MCP, workers. Schemas in `research_multiplier/schemas/`. |
| [docs/roadmap.md](docs/roadmap.md) | Tasks P0–P6 with acceptance criteria. Work on a task means meeting its criteria. |
| [docs/engineering.md](docs/engineering.md) | Tooling, workflow, the change checklist, versioning, tests. |

Each has a Chinese version (`*.zh-CN.md`). Update both when you change one.

## Stop and ask before

- Building on an **open question** (architecture section G).
- Changing a **settled decision**, or a **stable contract** in `docs/interfaces.md` or its schemas.
- Adding or changing **database migrations**. Migrations are forward-only, and merged migrations are never edited.
- Touching anything under **`_pinned/`** or **`_compat/`**. Those bytes back recorded fingerprints (B7).
- Changing **LICENSE, NOTICE or TRADEMARKS**, CI workflows, or release configuration.
- Adding anything that **sends research content outside the workspace**: external models, network calls or telemetry (D4).
- Adding a runtime **dependency** to the base install. It must stay dependency-free.

## Rules for every change

- Put business logic in `research_multiplier/workflows/` functions. CLI and HTTP handlers only parse and present (A2).
- Keep the kernel domain-independent. Add no new Lean-specific names or `kind IN (...)` lists to kernel code or SQL (A3, B3).
- Never print, log or commit credentials, DSNs or research content. Use temporary workspaces and disposable databases; never a real research project.
- User-facing interface strings go into message catalogs, English default and Chinese (E6). This applies after the React migration (P1.4).
- Follow the [change checklist](docs/engineering.md#change-checklist) and say in the PR why any item does not apply.

## Layout

| Path | Contents |
| --- | --- |
| `research_multiplier/cli/` | Argument parsing and output only |
| `research_multiplier/workflows/` | Research logic shared by the CLI and the API |
| `research_multiplier/storage/`, `migrations/` | PostgreSQL access, Alembic revisions and their SQL |
| `research_multiplier/workbench/` | Local workbench server and built interface assets |
| `research_multiplier/schemas/` | JSON Schemas for records and interface contracts |
| `research_multiplier/*/_pinned/`, `_compat/` | Fingerprinted verifier and adapter sources (do not edit) |
| `lean/` | Pinned Lean and Mathlib setup |
| `docs/` | User and design documentation |

## Commands

Development install:

```sh
python -m venv .venv && . .venv/bin/activate
python -m pip install -e ".[dev,postgres]"
```

Checks that CI runs on every push (`.github/workflows/tests.yml`):

```sh
ruff check .
python -m pytest -m offline
```

Build the wheel and run the offline smoke check that CI runs:

```sh
python -m pip wheel --no-deps --wheel-dir dist .
research-multiplier research init-workspace /tmp/rm-smoke
research-multiplier doctor --skip-lean
```

Regression lanes currently live in the external validation bundle. See [CONTRIBUTING.md](CONTRIBUTING.md#run-the-external-regression-suite). Run them from outside the checkout with an installed wheel, `PYTHONPATH` unset, and a disposable database for the PostgreSQL lanes. Roadmap task P0.2 moves the tests into `tests/`.

Open the workbench on a test project:

```sh
research-multiplier --workspace /path/to/test-project workbench
```

## Commits and pull requests

- Conventional Commits: `type(scope): summary`, one purpose per commit.
- Sign off every commit with `git commit -s` (Developer Certificate of Origin, E9).
- Branch from `main` as `type/short-topic`. Do not commit to `main` directly, and do not push or open a PR unless asked.
- Commands, decisions, model or network use that researchers will see also need updates to `research_multiplier/agent_guide.md`, both quickstarts, and `CHANGELOG.md`.
