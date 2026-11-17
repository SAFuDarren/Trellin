# Where the evidence is in this repository

Run commands from the repository root. Commands that call GitHub need `gh` signed in; if it is not, say so and score the affected checks from local evidence only. Never paste secret values into the record.

## 01 Requirements and specification
- `docs/roadmap.md`: tasks, owners, acceptance criteria and phase exit criteria.
- `docs/architecture.md`: direction, settled decisions, open questions (section G), non-goals.
- Do merged PRs close against roadmap tasks? `gh pr list --state merged --limit 30 --json number,title,body`.
- Issues and milestones: `gh issue list --state all --limit 100 --json number,title,milestone,labels,state` and `gh api repos/{owner}/{repo}/milestones`.

## 02 Software architecture
- `docs/architecture.md` (target structure and decisions), `docs/interfaces.md` (contracts), and the area table in `CONTRIBUTING.md`.
- Is there an up-to-date diagram or generated dependency graph? Is layering enforced in CI, for example an import-linter contract in `pyproject.toml`?
- Coupling indicators:
  - `grep -rc "'lean-core'" research_multiplier/migrations/sql | awk -F: '{s+=$2} END {print s}'`
  - `grep -rl "kind IN (" research_multiplier/migrations/sql | wc -l`
- Largest modules: `find research_multiplier -name '*.py' -not -path '*/_pinned/*' | xargs wc -l | sort -rn | head`.
- Change impact radius: for the last 10 feature PRs, count the top-level packages touched. `gh pr view N --json files` or `git show --stat`.

## 03 Data and state
- Migrations:
  - count and forward-only rule: `ls research_multiplier/migrations/versions | wc -l`;
  - `grep -L "raise NotImplementedError" research_multiplier/migrations/versions/*.py` should list no recent revision.
- Upgrade from the previous release: the release-check record, or a run of `storage.migrate.upgrade` from the previous head to the current head on a disposable database.
- Idempotency and concurrency:
  - `operation_key` and leases on `rm.jobs`;
  - `request_id` and `Idempotency-Key` handling in `workbench/server.py`.
- Recovery: `research-multiplier research backup-project`, `verify-project-backup` and `restore-project`, and whether a regression lane exercises them.

## 04 Code quality
- `ruff check .` and `ruff format --check . | tail -1`: how many files would be reformatted.
- Ruff rule breadth in `[tool.ruff.lint]` of `pyproject.toml`.
- Type checking: is pyright or mypy configured and run in `.github/workflows/`?
- Lock files: `uv.lock`, and later `web/pnpm-lock.yaml`.
- Dependency updates: `.github/dependabot.yml`.
- Technical debt: is there a maintained list (issues labelled tech debt, or a section in the roadmap)?

## 05 Testing and verification
- `python -m pytest -m offline -q`: count and duration.
- CI test jobs in `.github/workflows/tests.yml` and `checks.yml`, and their recent results: `gh run list --limit 20`.
- External validation bundle lanes (offline, postgres, release-lean), as described in `CONTRIBUTING.md`, and how many cases have moved into `tests/` (roadmap P0.2).
- Regression discipline: for recent `fix:` commits (`git log --oneline --grep '^fix'`), did each touch a test file?
- End-to-end interface tests: Playwright under `web/` (after P1.4).
- Reproducibility: `research-multiplier replay` on a recorded run; fingerprints of pinned verifiers.

## 06 Version control and collaboration
- Branch protection: `gh api repos/{owner}/{repo}/branches/main/protection`. A 404 means none.
- Reviews on merged PRs: `gh pr list --state merged --limit 20 --json number,reviews`.
- `.github/CODEOWNERS` against who actually reviews.
- DCO workflow results on recent PRs.
- Parallel work: branches or worktrees in use, and integrations without overwrites.

## 07 CI/CD and release engineering
- Clean install: the package smoke job in `checks.yml` (three operating systems), and the quickstart.
- Workflows triggered on push and pull_request, and their last results.
- Configuration separation: workspaces, credentials file, `RM_*` variables, disposable test databases.
- Releases: `git tag`, `gh release list`, `CHANGELOG.md`, `docs/release.md` hashes. Has a rollback or restore been rehearsed?

## 08 Reliability and observability
- Task traceability: `rm.research_task_status` failure codes, attempts, `lean_progress_history`, and the workbench activity view.
- Metrics: `research-multiplier advance metrics` and the evaluation harness.
- Logging: structured logs and request IDs (planned in docs/interfaces.md).
- Incidents: `docs/trust-incidents.md` and any recorded incident or post-incident review.

## 09 Security and dependency management
- Secrets: credentials live in the private credentials file outside the repository.
  - Scan tracked files by name only: `git grep -nIl -E "(api[_-]?key|secret|password|postgresql://[^ ]*:[^ ]*@)" -- . ':!docs' ':!*.md'`, then inspect each hit without printing values.
  - Use gitleaks if it is installed.
- Least privilege: the four database roles (human, scheduler, agent, verifier) and the external-model confirmation.
- Input validation: `tests/offline/test_workbench_server.py` and the JSON Schemas.
- Vulnerabilities: Dependabot alerts (`gh api repos/{owner}/{repo}/dependabot/alerts` if enabled), and `pip-audit` if available.

## 10 Project and task management
- The roadmap against issues, milestones and the project board (`gh project list`).
- Work in progress: open PRs (`gh pr list`) and in-progress issues.
- Decision routing: `needs-decision` issues and the open-question table.

## 11 Documentation and knowledge
- `README.md`, both quickstarts, `CONTRIBUTING.md`, `AGENTS.md`, and the `docs/` index.
- Drift: compare the last change date of `docs/architecture.md` and `docs/interfaces.md` with code changes to the areas they describe (`git log -1 --format=%cs -- path`).
- Handover: the result of stress test 6.

## 12 Engineering metrics
From GitHub, over the period since the previous audit:
- Lead time: `gh pr list --state merged --limit 100 --json createdAt,mergedAt`, median hours from opened to merged.
- Delivery frequency: merged PRs per week; releases (`gh release list`).
- Change failure rate: share of merged PRs followed by a revert or fix PR for the same change (`git log --oneline --grep -i revert`).
- Rework: share of `fix:` commits addressing changes from the same period.
- Record the figures in the audit so the next audit can compare them.
