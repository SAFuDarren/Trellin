# Engineering and project management

English · [简体中文](engineering.zh-CN.md)

How the project is built, changed, released and run as an open community project. Three documents work together:

| Document | Answers |
| --- | --- |
| [Architecture decisions](architecture.md) | What we build and why. Numbered decisions and open questions |
| [Roadmap](roadmap.md) | In what order, and how we know a task is done |
| [Interface contracts](interfaces.md) | The conventions and contracts others build against: API, events, plugins, MCP, workers, CLI |
| This document | How we work: languages, tools, process, community, releases |

When these documents disagree, the architecture decisions win; fix the other document in the same pull request.

---

## Repository layout

Use the current layout below when placing new files. Keep the root for project-wide entry points, contributor and license files, and shared tooling, packaging and release configuration. Put component-specific files in the directory that owns them. Update both language versions of this table when a directory changes purpose.

| Path | Contents | Language |
| --- | --- | --- |
| `research_multiplier/` | Python package: CLI, workflows, storage, policy, models, plugins shipped with the core, workbench server | Python |
| `research_multiplier/storage/`, `research_multiplier/migrations/` | Persistence code, Alembic revisions and the SQL they apply | Python, SQL |
| `research_multiplier/schemas/` | Machine-readable schemas for records and interfaces | JSON |
| `research_multiplier/workbench/app/` | Interface files built from `web/` and checked into the package. Regenerate from source when the frontend changes | Generated |
| `research_multiplier/*/_pinned/`, `research_multiplier/_compat/` | Exact verifier and adapter sources behind recorded fingerprints (B7) | Python |
| `lean/` | Generic Lean support and pinned Lean and Mathlib configuration | Lean |
| `web/` | React source under `src/`, browser tests under `e2e/`, frontend configuration and lockfile | TypeScript |
| `desktop/macos/` | App sources and resources, build and packaging tools, and native tests | Swift, shell |
| `brand/` | Product artwork and usage guidance | SVG, PNG, Markdown |
| `tests/` | Offline, PostgreSQL and Lean tests, the versioned `compat_validation/` suite, and small synthetic fixtures | Python |
| `scripts/`, `.github/` | Maintainer automation, CI workflows, and issue and PR templates | Python, YAML |
| `docs/` | User and engineering guides indexed in `docs/README.md`, reviewed images in `docs/images/`, and dated project audit summaries in `docs/audits/` | Markdown, images |

Keep real research projects, runnable public examples and documentation demos, personal skills, agent memory and raw audit materials in separate directories outside the software checkout. Keep test output, logs, screenshots produced by runs, database dumps, external validation bundles, and wheel or source distribution output outside it too. Use a disposable workspace and database for each test lane and a temporary output directory such as `RUNNER_TEMP` in CI. A local dependency environment or cache may stay in the worktree if Git ignores it. Review the paths in the PR diff before sending it.

---

## Languages and tooling

| Language | Used for | Version policy | Format and lint | Types | Tests |
| --- | --- | --- | --- | --- | --- |
| Python | Kernel, workflows, CLI, server, workers, plugin SDK | Support the Python versions in the CI matrix (currently 3.10 and 3.14); raising the minimum is a release note item | ruff | pyright: basic for the package, strict for new kernel modules | pytest |
| SQL (PostgreSQL) | Schema, invariants, security-barrier views, functions | The PostgreSQL major versions in the CI matrix, with pgvector | Reviewed in pull requests; naming rules below | Constraints and functions | `postgres` lane |
| TypeScript | React interface | Node LTS pinned in `.nvmrc` and the `packageManager` field; pnpm with a committed lockfile | ESLint and Prettier | `strict` tsconfig; API types generated from OpenAPI | Vitest and Playwright |
| Lean | Generic verifier support | Lean 4.19.0 and the pinned Mathlib; upgrade only as a dedicated change (below) | Lean conventions | Lean | `lean` lane |
| Plugin languages | Verifiers and tools outside the core | Declared in each plugin manifest | The plugin's own | The plugin's own | Plugin conformance suite |
| Shell, YAML | CI and deployment glue | — | shellcheck for scripts | — | Exercised by CI |

Rules by language:

- **Python**
  - The base install keeps zero runtime dependencies. Features that need packages go into extras such as `postgres`.
  - Use the development install in AGENTS. P0.3 introduces a committed `uv.lock`.
  - Never edit code under `_pinned/` that has produced recorded evidence. Add a new pinned version instead (B7).
  - Prefer Python over shell for scripts, because CI also runs on Windows.
- **SQL and migrations**
  - Migrations are forward-only. `downgrade()` raises and points to a backup restore; this is already the practice.
  - Never edit a migration that has been merged.
  - Each migration has one purpose and states it in its docstring.
  - After P2.1, no new domain `kind IN (...)` lists and no `'lean-core'` literals in kernel objects (B3, A3).
  - After P3.1, every new workspace-owned table needs a workspace ID column and a row-level security policy, both written in the same migration (D5).
  - Kernel objects live in the kernel schema, plugin objects in the plugin's schema (B6).
- **TypeScript and React**
  - No inline scripts or styles, and no external hosts, so the server's CSP stays strict.
  - Every user-facing string comes from message catalogs: English default, Chinese provided (E6).
  - API access only through the generated client. Never hand-write response types.
  - Server data is fetched and cached with TanStack Query; local interface state stays in components.
  - Accessibility target: WCAG 2.1 AA.
  - Use the interaction tokens in [`foundation.css`](../web/src/styles/foundation.css). Give each independently visible action area at most one filled `.button.primary`. Use outlined `.button` controls for secondary actions and quiet text or icon controls for tertiary actions. When a confirmation step opens within an area, it takes the primary emphasis.
  - Keep page, section, and card headings at 22, 16, and 14 px using the workbench type tokens. Reserve the Navy accent for selection, focus, primary actions, and running states. Use the theme-specific accent and foreground tokens together, and give tinted selection a solid accent indicator.
- **Lean**
  - A Lean or Mathlib upgrade is its own pull request. It records the new versions and runs the `lean` lane and the release regressions. It follows the existing upgrade path (`lean-reset-after-upgrade`, regression replay), and evidence from the old version is preserved, not rewritten.
- **Plugins**
  - Any language. A plugin must ship a manifest and pass the conformance suite (C1–C3).

### Where each new piece of code goes

| If you are adding… | Write it in | Because |
| --- | --- | --- |
| A research rule, record type or query | Python workflow function plus SQL | One implementation for the CLI and the API (A2); invariants in the database (B6) |
| A screen or interaction | TypeScript/React in `web/` | E1 |
| A verifier or domain checker | A plugin, in whichever language suits the domain | C1 |
| A tool for external agents | An MCP tool calling existing workflows | C4 |
| A separate high-concurrency component | Discuss first; Go or another language only with a recorded decision | E1 |

---

## Development workflow

1. **Issue first.** Every non-trivial change starts from a GitHub issue that states the problem, the acceptance criteria and the decision numbers involved. Small fixes may go straight to a pull request.
2. **Branch.** For a tracked task, use the claimed branch name `type/issue-short-topic`, for example `feat/12-event-log`. Never commit directly to `main`.
3. **Commits.** Use Conventional Commits: `type(scope): summary`.
   - Types: `feat`, `fix`, `docs`, `refactor`, `test`, `build`, `ci`, `chore`.
   - The scope is a layer or area, for example `kernel`, `lean`, `web` or `migrations`.
   - Keep each commit to one purpose.
4. **Pull request.** Fill in the template and the change checklist. The owner reviews submissions from other contributors and checks the evidence for their own PRs.
5. **Merge.** Only the GitHub repository owner squash-merges upstream PRs, including their own, after every required check passes. Keep the Conventional Commit title for the changelog.
6. **Decisions.** If a change needs a new architectural decision, or a change to a settled one, update `architecture.md` and `architecture.zh-CN.md` in the same pull request.
   - Give the decision the next number in its section.
   - When replacing a decision, mark the old one "superseded by X" instead of deleting it.
   - Open questions are answered only by the maintainer.

### Change checklist

Every pull request confirms each item, or says why it does not apply:

- [ ] The change follows the settled decisions it touches; cite the numbers.
- [ ] It does not build on an open question (`architecture.md`, section G).
- [ ] Business logic is in a workflow function, not in a CLI or HTTP handler (A2).
- [ ] Schema changes are a new forward-only migration. It upgrades from the previous release and keeps the required invariants and policies.
- [ ] Every write records the user and appends its event (B4, B5; after P1).
- [ ] Data leaving the workspace is authorized and recorded (D4).
- [ ] The OpenAPI document and the generated TypeScript types are updated (D1; after P1.2).
- [ ] All user-facing strings are in the English and Chinese catalogs (E6).
- [ ] Tests cover the change in the right lane, and CI passes.
- [ ] User docs are updated in English and Chinese; both quickstarts are updated if first use changed.
- [ ] `research_multiplier/agent_guide.md` is updated if commands, decisions, model use or network use changed.
- [ ] `CHANGELOG.md` has an entry for anything a user or plugin author would notice.
- [ ] No secrets, connection strings or research content appear in logs, URLs or test fixtures.

---

## Issues and planning

All tasks, bugs and milestones are tracked in **GitHub Issues** and a **GitHub Project** board.

- **Milestones** use the names of roadmap phases. A roadmap task becomes one issue or an epic with sub-issues. Keep existing roadmap task numbers, such as `P1.3`, for references.
- **Labels by scope and type** include `layer:kernel`, `layer:plugin`, `layer:collab`, `layer:ai`, `layer:entry`, `area:web`, `area:migrations`, `area:lean`, `area:docs`, `type:bug`, `type:feature`, `type:refactor`, `type:docs`, `type:chore` and `type:incident`. Use `needs-decision` for work blocked on an open question. Use `good first issue` and `help wanted` to invite contributions.
- **Priority.** Give each open issue one urgency label and pick up actionable work in this order: `priority:p0` for critical blockers that need immediate attention, `priority:p1` for urgent work in the current batch, `priority:p2` for work after higher priorities, and `priority:p3` for the lower-urgency backlog. At each priority, review and record existing results first, then claim the oldest eligible issue. Record a blocker and its dependency on the issue before moving to the next actionable item. Record the reason when changing an issue's priority. See the [GitHub queues](https://github.com/SAFuDarren/Trellin/issues/112#github-queues).
- **Board columns** progress from Triage to Ready, In progress, In review and Done. Use `status:triage` when scope, a dependency or a decision still needs clarification. Use `status:ready` when acceptance criteria, dependencies and [model and environment fit](#difficulty-and-model-fit) are clear. The claim comment and `status:in-progress` track ownership. Add `status:in-review` when a reviewable PR is open and retain any active claim. See [parallel work](#parallel-work).
- **Triage:** the maintainer reviews new issues weekly, applies labels, assigns a milestone or closes them, and links any decision that blocks them.
- **Roadmap upkeep:** when a task is added, split or reordered, update `roadmap.md` and `roadmap.zh-CN.md` together with the issues. Progress is tracked in the issues, not the roadmap; see [keeping status and records current](#keeping-status-and-records-current).

---

## Versioning and compatibility

Version 1.0.0 is the first public release. By default, each PR merged into main takes the next patch version, for example `1.0.22` → `1.0.23`. A maintainer-approved feature milestone takes the next minor version and resets the patch. An approved major milestone that opens the next generation, or a breaking change to the package's public behavior, takes the next major version and resets the minor and patch. Package version increments, tag creation, and release publication are separate actions.

Use the static `[project].version` in [pyproject.toml](../pyproject.toml) as the package version source. Update [the compatibility suite's version marker](../tests/compat_validation/PACKAGE_VERSION) to the same candidate version. Preserve its [original provenance](../tests/compat_validation/PROVENANCE.md) and archived baseline. The required release regressions verify the marker against the installed wheel.

Each public interface also carries its own version.

| Interface | Version marker | Rule |
| --- | --- | --- |
| Python package and CLI | Package version (semver) | A breaking change raises the major version and is announced in the changelog |
| HTTP API | Path prefix `/api/v1` | Additive changes stay in v1; a breaking change adds v2 and keeps v1 for at least one minor release |
| Plugin protocol | `protocol_version` in requests and manifests | The runner supports the current and previous protocol versions |
| Event payloads | `schema_version` per event type | Consumers ignore unknown fields; changing a field's meaning means a new version |
| Database schema | Alembic head revision | Forward-only; every release documents its predecessor revision |
| Verifiers | Name, version, source fingerprint | Evidence stays tied to the exact verifier that produced it (B7) |

New interfaces may be marked *experimental* in the docs. Experimental interfaces can change in a minor release, but the change must be listed in the changelog.

Deprecations warn for at least one minor release before removal.

---

## Testing strategy

| Lane | Needs | Runs | Covers |
| --- | --- | --- | --- |
| `offline` | Python only | Every pull request | CLI parsing, offline briefs, replay, pure logic, server request validation |
| `postgres` | Disposable PostgreSQL with pgvector | Every pull request | Migrations from empty and from the previous release, invariants, workflows, API routes, row-level security (after P3.1) |
| `lean` | Pinned Lean and Mathlib | Scheduled, and before every release | Lean verification, replay, upgrade paths |
| `web` | Node | Every pull request touching `web/` or the API | Vitest units, Playwright parity and section flows against the fixture server, and the layout sweep (`web/e2e/layout-sweep.spec.ts`), which checks every destination at three window sizes in both themes for page scroll, clipped text, cards outside or under canvas controls, a narrow canvas, underlined link buttons and small targets. A new destination is covered by adding it to `SECTIONS`. |
| `plugins` | Per-plugin environments | Every pull request touching the runner or plugins | Conformance suite for reference plugins |
| package smoke | Built wheel on Linux, macOS and Windows | Every pull request | Installation and first-use commands from an installed wheel (existing) |

Rules:

- Tests never touch a real research workspace or database. Use temporary workspaces and disposable databases.
- Fixtures contain no real research content and no credentials.
- A bug fix includes a test that fails before the fix.

---

## Engineering audits

At the end of every roadmap phase, run the engineering maturity audit with the maintainer-provided rubric kept outside the checkout. It scores 12 dimensions × 4 checks from repository evidence, runs or records six stress tests, and writes a record to [docs/audits](audits/README.md). Findings get permanent IDs. Each one becomes a roadmap task or issue, and the next audit reports its status. Use the trend between audits, not the absolute score, to decide where to invest.

## Parallel work

Several people and coding agents may work on the repository at once. The rules below keep their work from colliding; `AGENTS.md` carries the short version that every agent reads.

**Assigning work**
- Use your own GitHub identity. Contributors push to their own forks and open PRs against upstream `main`. Keep owner credentials out of Agent runtimes. The owner holds upstream write access and applies the same required checks to owner-authored and contributor PRs. See the [live GitHub rules](https://github.com/SAFuDarren/Trellin/rules).
- Request a claim on an open issue. Include GitHub login, fork repository, head branch, expected paths, model and effort or human-only work, and the tools needed for verification. The owner confirms the source and fit in the issue before work starts. Keep one active issue per person or Agent.

  From an upstream checkout, the owner records the confirmed fork request and scope.

  ```sh
  python scripts/tasks.py claim 123 --agent contributor-web --branch fix/123-short-topic \
    --repository contributor/Trellin --contributor contributor --scope web/src
  ```
- Use `python scripts/tasks.py board` in an upstream checkout for holders, branches and linked PRs. Claims older than 48 hours need a progress review. The owner records releases and resolves conflicting requests.
- Link the issue in the PR using `Closes #N` when complete and `Refs #N` for partial work. Task link verifies the PR author, source repository and branch against the owner-confirmed fork claim. A trivial PR without a task uses the owner-assigned `no-issue` label.
- Keep the claim through review. For a handoff, post the tested SHA, evidence, remaining work and next verification command. Record scope or model changes and obtain confirmation before continuing. Split overlapping work by a reviewable dependency boundary.

**Isolation**
- One task, one branch from upstream `main`, one git worktree. Nobody edits another worker's branch or worktree.
- Local resources are per worker:
  - each test database gets a unique name;
  - servers start on `--port 0` or a checked free port;
  - case copies, logs and build output stay at a temporary location outside the source tree, following [repository layout](#repository-layout).

**Hot spots that conflict easily**

| Hot spot | Rule | Enforced by |
| --- | --- | --- |
| Alembic migrations | Maintainer approval. Number the revision when the PR opens; renumber and fix `down_revision` if `main` moved | `tests/offline/test_migrations.py` (unique revisions, a single head, numbering matches file names) |
| Built interface `research_multiplier/workbench/app/` | Regenerate after every rebase; never merge by hand | Web CI job (the committed build must match the source) |
| Generated API types (after P1.2) | Regenerate after every rebase | CI diff check (P1.2) |
| Translation catalogs | Keep both sides' keys on conflict | Catalog parity test |
| Architecture decisions, interface contracts, schemas | Changed only in their own PR, approved by the maintainer | CODEOWNERS review routing and the owner's merge decision |

**Integration**
- Fetch and rebase on the latest upstream `main` before review and before merging. Run `python scripts/check_package_version.py` on the committed candidate and rerun required checks. If the upstream remote is named `upstream`, pass `--base upstream/main`. For a maintainer-approved increment, pass `--bump minor` or `--bump major`. If main advanced, choose the version from the new base and update both version files before checking again.
- Keep each PR to one task.
- Squash-merge with a Conventional Commit title.
- Issues found outside the task become new issues, not extra changes in the branch.

#### Owner merge preflight

Contributors can prepare PRs in parallel. The owner chooses the integration order and processes one merge at a time. Each PR reserves the next patch version when it is ready for this queue. If the base advances, the contributor rebases, updates both version markers and verifies the new head.

From a clean checkout of current upstream `main`, the owner reviews the diff and checks the exact commit with `python scripts/review_merge.py PR --head FULL_REVIEWED_SHA`. Supply the PR number and the complete reviewed SHA. With the same head, add `--merge` to apply the squash merge. The command reads PR and issue metadata from GitHub, verifies the latest base and version, and rechecks the owner-confirmed claim even if a prior Task Link run is already green. It requires successful checks from the configured sources on the reviewed head. The existing GitHub rules apply to the merge request.

Approve a first-time contribution's CI after reviewing its changed files. For the first outside merge, complete the license decision in [#24](https://github.com/SAFuDarren/Trellin/issues/24). Outside changes to workflow definitions or merge verification require separate review before using `--reviewed-workflow-changes`. Run verification from the protected checkout. The candidate is read as Git objects and metadata, while the isolated PR CI executes its tests.

For newcomer acceptance, let a contributor with a separate GitHub account take a [good first issue](https://github.com/SAFuDarren/Trellin/labels/good%20first%20issue) and follow the public guide from clone to a reviewable PR. Record the claim, signed commit, fixture result, CI approval, one review or handoff, and the contributor and owner permission outcomes in [#267](https://github.com/SAFuDarren/Trellin/issues/267). Include setup time, CI retries and active reviewer time for the delivery audit in [#23](https://github.com/SAFuDarren/Trellin/issues/23).

### Difficulty and model fit

Before marking an issue ready, record the baseline SHA, scope, prerequisites, reproduction, acceptance criteria and required verification environment. Score each dimension from 0 to 3 independently of urgency.

| Dimension | 0 | 1 | 2 | 3 |
| --- | --- | --- | --- | --- |
| Scope | Local doc or test | One component | Several related components | Cross-layer or release boundary |
| Uncertainty | Known fix | Clear reproducer | Several plausible fixes | Open contract or unclear problem |
| Impact | Trivial rollback | Local behavior | Persisted state or compatibility | Access, migration or proof integrity |
| Verification | One deterministic check | One lane | Multiple lanes or services | Native device, exact replay or migration rehearsal |
| Dependencies | None | One prerequisite | Coordinated changes | Sequenced rollout or independent approval |

Use the [model registry](../.github/agent-models.json) to apply the initial floors in the [Artificial Analysis Coding Agent Index v2.1](https://artificialanalysis.ai/agents/coding-agents/comparisons/codex-vs-claude-code#models). Compare the published Agent, model and effort together. Run `python scripts/model_routing.py D2` for the current eligible configurations, replacing D2 with the issue level. Keep the benchmark version and date with the fit decision.

| Level | Issue points | Approximate index floor | First Codex recommendation |
| --- | ---: | ---: | --- |
| D1 | 0–3 | 30 | GPT-6.2 Sol low, 37 |
| D2 | 4–7 | 45 | GPT-6.2 Sol medium, 45 |
| D3 | 8–11 | 50 | GPT-6.2 Sol xhigh, 50, or GPT-6.2 Astra high, 52 |
| D4 | 12–15 | 52 | GPT-6.2 Astra high, 52, with independent verification |

Cross-layer persistent-state design, native Mac acceptance and Lean replay are at least D3. Merge authority, CI trust, protected migrations and irreversible research evidence are D4. The floors guide routing. D3/D4 still require owner confirmation of scope and evidence, and D4 requires a separate verification step.

Choose the lowest-cost eligible configuration with the required skills and tools. Match Web work to React/TypeScript, keyboard checks and Playwright. Use the [offline, PostgreSQL and pinned Lean test lanes](../tests/README.md) for backend and proof changes, and the [desktop guide](desktop.md) for macOS acceptance. Review access policy against GitHub Rulesets, fork permissions and signed commits. If a configuration is unlisted, request an owner exception with task-relevant evidence. If a required tool is unavailable, hand off acceptance to someone with that environment or take a separately scoped reproduction or review. Verification evidence remains mandatory.

The [Model catalog workflow](../.github/workflows/model-catalog.yml) checks the OpenAI, Anthropic and Google catalogs daily and opens a pending score issue for each new supported ID. For a release or snapshot missing from the tracked catalogs, use the [Model score update form](https://github.com/SAFuDarren/Trellin/issues/new?template=model_score.yml) to register it. Use the title `Model score update — Provider / exact-model-id` so the catalog workflow can recognize the same request. Submit a registry PR with the model and effort, benchmark version, date, score and source. Use the existing registry until the owner merges the refreshed values. Revisit recommendations when the benchmark version changes or a configuration retires.

### Branch lifecycle and weekly inventory

Run `python scripts/tasks.py branches` from a repository checkout. The command reads live `origin` branch heads, local branches, worktrees, GitHub PRs, and held claims. Add `--json` to keep a dated snapshot. The maintainer reviews the inventory weekly and uses the issue to return stale branches and due preservation reviews to their owners.

The [Branch inventory workflow](../.github/workflows/branch-inventory.yml) updates one remote report in [#205](https://github.com/SAFuDarren/Trellin/issues/205) each week and on manual dispatch. The report lists candidates for review. Use a local checkout for the worktree and commit coverage checks.

| State | Evidence | Who acts |
| --- | --- | --- |
| Active | An open PR, an open PR using the branch as its base, or a held claim with recent progress. A new claim has 48 hours to make progress. Recent unclaimed tips and tips without a verifiable commit date are held for review. | The task owner continues the work or hands it off. |
| Ready to retire | The current tip exactly matches the final head of a merged or explicitly closed PR. No open PR depends on the branch, and the branch has no valid claim. | The owner follows the [retirement procedure](#retire-a-branch-and-worktree) on the day the task ends. The maintainer decides on unmerged work. |
| Stale | No active PR or valid claim and no commit in the last seven days, or a claim older than 48 hours with no recent commit after it was made. | The owner records what to preserve or proposes retirement. The maintainer handles unowned entries. |
| Preserved | The repository default branch or an entry in [the preservation register](preserved-branches.json). A detached or unborn checkout is held for manual review. | The named owner reviews the recorded reason on its review date. The maintainer follows up on entries due in the weekly review. |

For a registered branch, record `branch`, `reason`, `owner`, and `review_on` in YYYY-MM-DD format. Register only confirmed decisions. For a squash merge, use the recorded PR head and merge commit rather than the ancestry of the old branch tip.

The report flags dirty or unavailable worktree status, ignored files, locked or prunable worktrees, and local commits that are unreachable from the live branch heads of every configured remote. It reports unknown reachability when remote heads or local history are incomplete. The inventory reads state without fetching, pruning or removing refs. Use the separate retirement checks before taking any action.

### Retire a branch and worktree

The owner handles local checkout cleanup or arranges an explicit handoff in the issue. Record the branch, reviewed commit and remaining work there. Keep the claim while you own review follow-up. Release it on handoff or when you relinquish follow-up. GitHub settings remain a maintainer decision.

**Remove a local worktree.** Confirm which worktree and task it serves. Use `git -C "$worktree" status --short --untracked-files=all --ignored=matching` and check for pending Git operations and active users or processes. Preserve useful changes and artifacts at the recorded recovery location, then verify the commit or files can be recovered. A maintainer-authorized handoff can remove a clean, unused checkout of an open PR while the branch and claim stay in place. Run `git worktree remove "$worktree"` from another checkout.

**Retire a branch.** Keep an open task branch. After integration, use `gh pr view "$pr" --json state,headRefOid,mergeCommit,baseRefName` to verify `MERGED` and record the final reviewed head. Fetch the target and verify that the reported merge commit is in its history. A squash merge does not preserve the original head in the target history. After explicit closure without merge, record the preserved work and get the maintainer's retirement decision.

Check open PRs with `gh pr list --state open --head "$branch"` and `gh pr list --state open --base "$branch"`. With stacked changes, the dependent owner retargets to the integrated base, reapplies only that PR's commits and runs the required checks before the parent branch retires. Compare the local and live remote tips with the reviewed commit and preserve any extra work. Once the task is closed or released and the checks pass, try `git branch -d "$branch"`. Leave a refusal for the maintainer. The owner may remove a verified remote task branch after one last check for dependent PRs.

After a merge, the maintainer previews eligible upstream branches with `python scripts/retire_merged_branches.py` from a full checkout. Fetch the current upstream `main` first. Add `--protect-checkout PATH` for each other local clone with worktrees to protect. The preview confirms that the merge is reachable from current `main` and GitHub still serves the exact original tip at the PR head ref. Review external references and check with any other worker holding the branch. To retire a reviewed branch, rerun with the same protected checkouts and `--apply BRANCH`. The command rechecks eligibility and requires the exact tip in the remote deletion lease. Record the PR, original tip and merge commit in the task for recovery. The maintainer leaves GitHub's automatic head deletion disabled to retain explicit review of stacked PRs and checked-out branches.

### Stress tests

Before relying on several agents for a phase, rehearse a same-issue claim race and the independent integration of two non-overlapping changes. Verify that one claimant wins, workers use separate worktrees and test resources, and both changes pass the required checks after integration. Record the result in the [engineering audit](audits/README.md).

For the repository-only [handover exercise #9](https://github.com/SAFuDarren/Trellin/issues/9), ask the next agent to find the source, generated interface, test and output paths from `AGENTS.md`, then name what would block retirement of a task branch. Record the result in the issue.

## Keeping status and records current

Each kind of record has one home. Update it in the same pull request that changes the facts.

| Record | Single source of truth | When to update it |
| --- | --- | --- |
| Task status and results | The original GitHub issue and its milestone | Record ownership through the claim tool. [Hand off reviewable results](#hand-off-a-result) on the issue. Complete [issue closure](#closing-issues) after acceptance |
| Plan and acceptance criteria | `docs/roadmap.md` and `.zh-CN.md` | Only when a task is added, split, reordered or its criteria change. Never edit it to mark progress. Update the issue in the same PR |
| Tracking issues (for example the publication gate, #18) | The checklist in that issue | Tick an item when the PR that completes it is merged |
| Decisions and open questions | `docs/architecture.md` and `.zh-CN.md` | When a question is answered: move it from section G to the "Decided" line, add or change the numbered decision, close its `needs-decision` issue |
| Interface contracts | `docs/interfaces.md` and the schemas | Follow "Changing a contract" in that document |
| User-visible changes | `CHANGELOG.md` | Record user- or plugin-author-visible changes under the candidate package version at the top |
| Package version | `pyproject.toml` | Choose the candidate against current main using the [versioning rules](#versioning-and-compatibility), and update the compatibility marker together. Follow [Prepare a release](release.md) when publishing |
| User guides | `docs/` (both languages), both quickstarts, `research_multiplier/agent_guide.md` | Whenever a command, screen, decision, model use or network use that researchers see changes |
| Audit findings | The audit record in `docs/audits/` (never edited afterwards) | The next audit reports each finding's status. Progress happens in the issue or task the finding names |

A milestone is closed when all its issues are closed and its phase exit criteria hold.

### Hand off a result

Update the original issue when there is a result to review or test. Use a `Result and acceptance` section so the next person can find the linked PR or trial build, the exact commit or build tested, the verification evidence, and any remaining acceptance work. Refresh that section when a new result supersedes it. Keep the issue open while the remaining acceptance work is pending.

Apply `status:in-review` when a reviewable PR is open. Keep the claim while you own review follow-up. If you hand off that responsibility, record the branch, revision and remaining work in the issue before releasing the claim with the claim tool.

## Closing issues

An issue is done when every acceptance criterion holds and a test or a check in CI shows it. Close it as soon as that is true; an open issue that is already resolved misleads everyone who reads the board.

- **Closed by its pull request.** Write `Closes #N` in the PR description only when the PR meets every criterion. Merging then closes the issue. If the PR meets only some criteria, write `Refs #N` and list in the PR what remains.
- **Closed by hand.** When the criteria are met by several PRs, by work done for another issue, or without code, close the issue yourself with a comment that names the PRs or commits and how each criterion is shown: `gh issue close N --comment "..."`.
- **After every merge**, check the issues the PR touched: the linked issue is closed, `status:in-progress` is removed, and tracking-issue checkboxes (for example #18) are ticked. Also look for other open issues the merge resolved, and close them with a comment.
- **Not done.** Never close an issue with an unmet criterion. Comment on what remains instead, or split the rest into a new issue and link it.
- **Not planned.** Close an issue that will not be done as "not planned", with the reason.
- **Reopen** an issue when a regression shows it was not done, and link the regression.

## CI runs

- **Every job has a time limit.** Each job sets `timeout-minutes` at two to three times its usual duration. A new job must set one. GitHub's default of six hours turns a hang into a lost afternoon.

  | Job | Usual time | Limit |
  | --- | --- | --- |
  | Lint and offline tests | 1 min | 15 min |
  | Web interface (React) | 2 min | 20 min (end-to-end step 10 min) |
  | Installed package smoke check | 1 min | 20 min |
  | Required release regressions | 14 min (2 min when scoped out) | 30 min |
  | DCO | under 1 min | 5 min |

- **Long regressions run where they can matter.** Every pull request builds the wheel, checks the version marker and compares the CLI against its baseline. The PostgreSQL and Lean regressions in the same job are skipped for a pull request that only touches `web/`, `research_multiplier/workbench/app/`, `docs/`, `brand/`, `desktop/`, Markdown files at the root, issue and pull request templates, `tests/compat_validation/PACKAGE_VERSION`, or the version line of `pyproject.toml`. Any other path runs them, and so does every push to `main`, the weekly schedule and a manual run. The job summary says which applied. Extend the list only when a path cannot affect the database boundary or proof checking.
- **Do not wait on a hung job.** A job that runs well past its usual time with no new log output is hung. Cancel the run (`gh run cancel ID`), read its log (`gh run view ID --log`), and either fix the cause or rerun once (`gh run rerun ID --failed`). If it hangs again, treat it as an incident (below) and fix the cause before merging.
- **Do not download what the runner already has.** Prefer tools on the runner image (for example its Google Chrome for the end-to-end tests) and pinned versions over downloads during the run.
- **Merge only on green.** Never merge with a required check pending or failed, and never disable, skip or loosen a check to make it pass. A flaky check gets an issue and a fix.

## Engineering incident reviews

When something in the engineering process fails in a way that cost time or could recur (a hung or broken CI run, a broken release, a bad merge, lost work, a leaked secret), open an issue with the **Incident review** template within a day. Keep it short and blameless. This is separate from research [trust incidents](trust-incidents.md), which concern recorded verification results.

| Section | What to write |
| --- | --- |
| What happened | One or two sentences, with dates and times in UTC |
| Impact | What was delayed, broken or put at risk, and for how long |
| Detection | How it was noticed, and how it could have been noticed sooner |
| Root cause | The underlying cause, not only the trigger |
| Fix | The PR or commit that fixed it |
| Follow-up | Issues for anything that prevents a repeat: a test, a CI check, a rule in `AGENTS.md` |

Close the review when its fix is merged and every follow-up is an issue of its own. Recurring causes go into the next [engineering audit](#engineering-audits).

## Releases

Follow [Prepare a release](release.md). In addition:

- The changelog is drafted from merged Conventional Commit titles, then edited for users.
- Release notes list:
  - database revisions: predecessor and new head;
  - supported Python, PostgreSQL and Lean versions;
  - interface version changes (API, plugin protocol, events);
  - deprecations.
- Docs in English and Chinese are updated before the tag.
- From P6 onward, release artifacts are signed, and SHA-256 sums are published.

---

## Open community

- **Code of conduct:** Contributor Covenant, in `CODE_OF_CONDUCT.md`.
- **Security:** `SECURITY.md` gives a private reporting channel. Fix confirmed issues before public disclosure.
- **License and sign-off:** Apache-2.0, with the Developer Certificate of Origin (E9). Every commit carries `Signed-off-by: Name <email>` (`git commit -s`), and CI rejects pull requests with unsigned commits. Review the license once more before merging the first outside pull request, because changing it afterwards needs every contributor's consent.
- **Code owners:** `CODEOWNERS` assigns reviewers per area (kernel, migrations, web, Lean, plugins). Changes to migrations, security code and `_pinned/` always need the maintainer's review.
- **Governance:** the maintainer decides open architecture questions and releases. Proposals for new decisions come as pull requests to `architecture.md`.
- **Paid extensions (if a product follows):** they live in a clearly separated directory or repository under their own license, and the Apache-2.0 core never depends on them.
- **License headers:** new source files start with an SPDX line, for example `# SPDX-License-Identifier: Apache-2.0`. Files under `_pinned/` keep their recorded bytes and are not edited to add headers (B7).
- **Name and logo:** follow [TRADEMARKS.md](../TRADEMARKS.md); forks and hosted services use their own names.
- **Third-party licenses:** new dependencies must use licenses compatible with the project's license. CI checks Python and npm dependency licenses against an allowlist. Mathlib is Apache-2.0; keep its notices.
- **Good first issues:** keep a few small, well-specified issues labelled for newcomers.

---

## Cross-cutting considerations

Check these when designing any feature:

- **Security:**
  - Credentials and connection strings never appear in logs, URLs, events or error messages.
  - The local server binds to loopback only and requires the session token.
  - Every write is authorized on the server, not only hidden in the interface.
- **Privacy:**
  - Nothing leaves a workspace without authorization (D4).
  - Telemetry, if ever added, is opt-in.
  - Logs record identifiers, not research text.
- **Reproducibility:**
  - Evidence records exact versions and hashes of everything it used (B1, B7).
  - Upgrades never rewrite past evidence.
- **Data ownership:**
  - Researchers can export a workspace's records and artifacts in documented formats.
  - Backups are restorable and tested in the `postgres` lane.
- **Internationalization:**
  - Interface text comes from catalogs (E6).
  - Dates, numbers and plurals are formatted by locale.
  - Research content is stored as entered and never machine-translated silently.
- **Accessibility:** WCAG 2.1 AA, full keyboard use, and checks in Playwright.
- **Performance:**
  - Initial interface bundle under 300 KB gzip.
  - Main pages respond in under 300 ms on the fixture database.
  - Heavy computation runs in workers or plugins, never in request handlers.
- **Platforms:**
  - The CLI and the local workbench support Linux, macOS and Windows.
  - Lean workflows support macOS and glibc Linux, as documented.
  - Group servers target Linux with Docker.
- **Cost control:** model calls respect project and workspace budgets, and limits stop dispatch instead of only warning.
- **Observability:** structured logs with request and job IDs; health endpoints for the server and workers (P3.5).
