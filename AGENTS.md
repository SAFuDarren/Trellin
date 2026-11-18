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
- CI: merge only when every required check passes. A job far past its usual time with no new log output is hung: cancel it, read the log, fix the cause or rerun once; do not wait it out. Every job sets `timeout-minutes`. See [CI runs](docs/engineering.md#ci-runs).
- When something in the engineering process fails (a hung CI run, a bad merge, lost work), open an **Incident review** issue. See [engineering incident reviews](docs/engineering.md#engineering-incident-reviews).

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

1. **Claim before you start, with the claim tool.** Every agent uses the same GitHub account, so pick a short name for this session (for example `claude-web` or `codex-backend-1`) and use it for every claim.
   - See who holds what: `python scripts/tasks.py board`.
   - Claim: `python scripts/tasks.py claim <issue> --agent <name> --branch <type>/<issue>-<topic> --scope <paths>`. The tool refuses issues that someone else holds or that need a decision. If two agents claim at the same moment, the later one withdraws automatically: start work only when the tool says `Claimed`.
   - Release when you stop: `python scripts/tasks.py release <issue> --agent <name> --reason "..."`.
   - Work only on an open issue. If no issue exists for what you were asked to do, ask the maintainer instead of starting.
   - Link the claimed issue in the PR with `Closes #N` when all criteria are met, or `Refs #N` for a partial change. CI checks the linked task and branch.
2. **One task, one branch, one worktree:** `git worktree add ../rm-<issue>-<topic> -b <type>/<issue>-<topic> origin/main`. Use the exact branch name from your claim. Never edit another agent's worktree or branch.
3. **Stay inside your scope.** List the files you expect to change in the issue. If you need a file that another open PR changes (`gh pr list`, then check its files), stop and coordinate in the issues instead of editing it.
4. **Shared hot spots:**
   - **Migrations**: need the maintainer's approval. Take the next number only when you open the PR. If `main` gains a migration first, renumber yours and update `down_revision`; CI rejects duplicate revisions and multiple heads.
   - **Generated files** (the built interface in `research_multiplier/workbench/app/`, and later generated API types): never merge them by hand. After rebasing, regenerate them and commit the result.
   - **Translation catalogs**: on a conflict keep both sides' keys; the catalog test checks that English and Chinese match.
   - **Decisions, contracts and schemas**: change them only in a PR of their own that the maintainer approves.
5. **Local resources are not shared:**
   - Give every test database a unique name, such as `rm_test_<branch>_<random>`.
   - Start servers with `--port 0` or a port you checked is free.
   - Put case copies, logs and build output in a temporary location outside the source tree. See [file placement](docs/engineering.md#repository-layout).
   - Never stop a process you did not start.
6. **Integrate small and often.** Rebase on `origin/main` before asking for review and again before merging. Keep each PR to one task.
7. **Finish cleanly.** Write `Closes #N` in the PR only when it meets every acceptance criterion, otherwise `Refs #N`. After the merge, check that the issue is closed and `status:in-progress` is removed, and close any other issue the merge resolved, with a comment naming the PR. Never close an issue with an unmet criterion; see [closing issues](docs/engineering.md#closing-issues). In the same PR, update every record the change affects: the `CHANGELOG.md` line, user guides in both languages, tracking-issue checkboxes, decisions. The table in [keeping status and records current](docs/engineering.md#keeping-status-and-records-current) lists where each record lives. Never edit the roadmap to mark progress. Report problems outside your task as new issues; do not fix them in your branch.
8. **Retire only your own branch and worktree.** Follow [branch retirement](docs/engineering.md#retire-a-branch-and-worktree) after review and integration. Check for unpublished commits, local files and dependent PRs before removing anything.

## Layout

Use the [repository layout and artifact rules](docs/engineering.md#repository-layout) when adding or moving files.

| Path | Contents |
| --- | --- |
| `research_multiplier/cli/` | Argument parsing and output only |
| `research_multiplier/workflows/` | Research logic shared by the CLI and the API |
| `research_multiplier/storage/`, `research_multiplier/migrations/` | PostgreSQL access, Alembic revisions and their SQL |
| `research_multiplier/workbench/` | Local workbench server and its generated `app/` |
| `web/` | React source, build configuration and browser tests |
| `tests/` | Python checks and small shared test fixtures |
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

React interface in `web/` (Node version in `.nvmrc`; pnpm through npx, no global install needed):

```sh
cd web
npx pnpm@9.15.4 install --frozen-lockfile
npx pnpm@9.15.4 typecheck && npx pnpm@9.15.4 lint && npx pnpm@9.15.4 test
npx pnpm@9.15.4 build   # writes research_multiplier/workbench/app; commit the result
```

`research-multiplier workbench` serves the built interface. Interface strings live in `web/src/i18n/en.json` and `zh-CN.json`; lint rejects literal text in JSX.

Build and install the wheel in a clean environment, then check offline initialization. This example uses a POSIX shell and virtual environment paths.

```sh
scratch=$(mktemp -d "${TMPDIR:-/tmp}/rm-smoke.XXXXXX")
python -m pip wheel --no-deps --wheel-dir "$scratch/wheels" .
python -m venv "$scratch/venv"
"$scratch/venv/bin/python" -m pip install "$scratch"/wheels/research_multiplier-*.whl
(
  cd "$scratch" || exit
  unset PYTHONPATH
  ./venv/bin/research-multiplier research init-workspace "$scratch/workspace"
  ./venv/bin/research-multiplier --workspace "$scratch/workspace" init "$scratch/workspace/inputs/question.json"
)
```

See [tests/README.md](tests/README.md) and the [contributor guide](CONTRIBUTING.md#run-the-external-regression-suite) for the regression lanes. Follow the lane instructions for an installed wheel and a disposable workspace or database.

Open the workbench on a test project:

```sh
research-multiplier --workspace /path/to/test-project workbench
```

## Commits and pull requests

- Conventional Commits: `type(scope): summary`, one purpose per commit.
- Sign off every commit with `git commit -s` (Developer Certificate of Origin, E9).
- Branch from `origin/main` using the name recorded in your issue claim. Do not commit to `main` directly, and do not push or open a PR unless asked.
- Commands, decisions, model or network use that researchers will see also need updates to `research_multiplier/agent_guide.md`, both quickstarts, and `CHANGELOG.md`.
