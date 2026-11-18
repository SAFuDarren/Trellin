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

## Engineering principles

These come from the [engineering audit](docs/audits/README.md) and apply to every task:

- **Keep the change impact radius small.** Before coding, list the modules you expect to touch. If a feature needs changes across many unrelated modules, stop and propose a design or interface change first.
- **Evidence before "done".** A task is done only when its acceptance criteria are met and an automated test shows it. Run the tests and report their real output.
- **Every bug fix adds a regression test** that fails before the fix.
- **Tasks are independently acceptable.** Split work so that each piece can be reviewed, tested and merged on its own, not by file count.
- **One implementation per behaviour.** Search for an existing function before writing a new one; extend it instead of duplicating it.
- **Results stay reproducible.** Research outputs must be reproducible from the recorded code version, parameters, dependencies and data. Never rewrite recorded evidence or pinned verifier sources.
- **Rules belong in CI.** When a rule keeps being missed, propose a check that enforces it instead of another paragraph of documentation.

To run the periodic maturity audit, use the project skill `.claude/skills/engineering-audit/` ("run an engineering audit"). Agents without skill support can follow its `SKILL.md` directly. Audit findings are tracked in `docs/audits/` and as roadmap tasks.

## Working in parallel with other agents

Several agents and people may work on this repository at the same time. Follow these rules so their work does not collide. Details are in [engineering](docs/engineering.md#parallel-work).

1. **Claim before you start.** Work only on an open GitHub issue for a roadmap task. Check that it is not assigned or labelled `status:in-progress`. Then add that label and comment with your branch name. If no issue exists, ask the maintainer instead of starting.
2. **One task, one branch, one worktree:** `git worktree add ../rm-<topic> -b <type>/<topic> origin/main`. Never edit another agent's worktree or branch.
3. **Stay inside your scope.** List the files you expect to change in the issue. If you need a file that another open PR changes (`gh pr list`, then check its files), stop and coordinate in the issues instead of editing it.
4. **Shared hot spots:**
   - **Migrations**: need the maintainer's approval. Take the next number only when you open the PR. If `main` gains a migration first, renumber yours and update `down_revision`; CI rejects duplicate revisions and multiple heads.
   - **Generated files** (the built interface in `research_multiplier/workbench/app/`, and later generated API types): never merge them by hand. After rebasing, regenerate them and commit the result.
   - **Translation catalogs**: on a conflict keep both sides' keys; the catalog test checks that English and Chinese match.
   - **Decisions, contracts and schemas**: change them only in a PR of their own that the maintainer approves.
5. **Local resources are not shared:**
   - Give every test database a unique name, such as `rm_test_<branch>_<random>`.
   - Start servers with `--port 0` or a port you checked is free.
   - Keep temporary files in your worktree or the system temp directory.
   - Never stop a process you did not start.
6. **Integrate small and often.** Rebase on `origin/main` before asking for review and again before merging. Keep each PR to one task.
7. **Finish cleanly.** Link the PR in the issue and remove `status:in-progress` once it is merged or handed back. Report problems outside your task as new issues; do not fix them in your branch.

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
