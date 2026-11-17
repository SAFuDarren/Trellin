# Checklist: 12 dimensions × 4 checks

Score each check 0, 1 or 2 under the scoring discipline in `SKILL.md`. For each dimension, "2 looks like" describes the evidence that justifies full marks; "Failure signals" are symptoms that cap the score at 0 or 1 even when documents exist.

## 01 Requirements and specification
Does the project know what it is building and when a piece is done?

1. Goals, scope and non-goals are written down.
2. Every important feature has checkable acceptance criteria.
3. Requirements are prioritized: must, planned, not now.
4. When a requirement changes, its effect on features, interfaces, data and tasks is analysed before work continues.

2 looks like: roadmap tasks with acceptance criteria that recent PRs actually closed against; recorded non-goals; impact notes on changed decisions.
Failure signals: requirements re-explained for every feature; plans change mid-implementation; agent output differs from intent.

## 02 Software architecture
Is there a stable structure, or only a collection of features?

1. An architecture diagram that matches the current code shows module responsibilities and dependencies.
2. Core modules have clear boundaries: no significant import cycles or casual reaches into other modules' internals.
3. Public interfaces, data contracts, error semantics and architectural constraints are specified.
4. An ordinary feature change stays within a few related modules.

2 looks like: a current diagram or generated dependency graph; layering enforced by a tool in CI; interface contracts that code and tests follow; recent feature PRs with small module spread.
Failure signals: one change touches many unrelated modules; interfaces change ad hoc; global state; directories that are not real boundaries.
Key measure: change impact radius, not module count.

## 03 Data and state
Is internal state explicit, consistent and traceable?

1. Core data models, state transitions and invariants are defined.
2. Every important piece of data has one authoritative source and an owner.
3. Data format and schema changes have compatibility checks and a migration strategy.
4. Repeated execution, concurrent modification, retries and recovery of important data are handled deliberately.

2 looks like: invariants enforced by the database; tested migrations including upgrades from the previous release; idempotency keys and leases; a backup restore that has been exercised.
Failure signals: several modules hold the same state; restarts corrupt state; retries duplicate work; old results become unreadable after a format change.

## 04 Code quality and maintainability
Can the code be changed for years without rewrites?

1. Consistent style, layout, naming, typing and error handling, enforced by tools.
2. No serious duplication of core logic, oversized modules or unexplained implicit dependencies.
3. Dependencies and the runtime environment are controlled: versions are traceable and upgrades can be verified.
4. Technical debt is recorded and managed by risk, neither ignored nor answered with wholesale rewrites.

2 looks like: formatter, linter and type checker in CI; a lock file; automated dependency updates; a maintained debt list.
Failure signals: each agent writes in a different style; the same function is implemented twice; small features need large refactors first.

## 05 Testing and verification
After a change, is there good reason to believe the system still works?

1. Key logic has automated unit tests.
2. Important interfaces and core flows have integration or end-to-end tests.
3. Every significant bug fix adds a regression test.
4. Tests are stable and repeatable, and cover edge cases, error paths and performance in proportion to risk.

2 looks like: tests running on every PR; bug-fix PRs that include tests; no flaky tests; for research code, also numeric precision, seeds, pinned environments, benchmark instances and reproducibility.
Failure signals: an agent claims "done" without independent tests; only happy paths are tested; changing A silently breaks B.
Coverage percentage is not correctness. Prefer key behaviours, contracts, invariants and real failure scenarios.

## 06 Version control and collaboration
Can several people or agents work at once safely?

1. All changes go through Git, and the main branch is always buildable.
2. Important changes get independent review and required checks before merging.
3. Parallel tasks have defined scope, owners and conflict coordination.
4. Developers and agents work in separate branches, worktrees or environments and integrate safely.

2 looks like: branch protection with required checks and reviews; PR history with reviews; CODEOWNERS that matches the people who actually review; parallel work that has been integrated without overwriting.
Failure signals: agents edit the same core files at once; integrations overwrite each other; history cannot locate a regression.

## 07 CI/CD and release engineering
Does code move reliably from development to use?

1. A clean environment can install, build and run the project by following written steps.
2. Every commit or PR automatically builds, tests and runs static checks.
3. Development, test and production configurations are separated and managed.
4. Releases are traceable, recovery or rollback is planned, and irreversible steps such as migrations have specific procedures.

2 looks like: CI on every push; release records with hashes; a rehearsed rollback or restore.
Failure signals: it runs on one machine only; releases need manual config edits; every upgrade is a gamble; no known way back to the last good version.

## 08 Reliability and observability
When something fails, can it be noticed, explained and recovered?

1. Key processes produce structured logs, errors and the necessary metrics.
2. Task execution can be traced to the module and stage where it failed.
3. Important outcomes (task success rate, latency) have targets, and regressions are detected.
4. Major failures have a recovery procedure, an owner and a post-incident review.

2 looks like: per-task records with failure codes; dashboards or metrics commands; incident reports. Long-running services may add SLIs, SLOs and an error budget.
Failure signals: failures without reasons; hung tasks that can only be rerun; debugging by guessing.

## 09 Security and dependency management
Can configuration, permissions or third-party code create uncontrolled risk?

1. Keys, passwords and tokens never enter the repository or logs.
2. Users, services and agents have least-privilege access.
3. External input is validated, and important operations have authentication, rate or resource limits.
4. Dependency sources, vulnerabilities, upgrade risks and abandonment risks are identified and handled.

2 looks like: secret scanning; separated database roles; tested input validation; automated vulnerability alerts that someone acts on.
Failure signals: tokens in code; agents with production rights they do not need; external input reaching dangerous operations; upgrades that break the system.

## 10 Project and task management
Does the project move forward deliberately?

1. There is a roadmap with phase goals and current priorities.
2. Tasks have a goal, scope, dependencies, completion criteria and an owner.
3. The critical path, blockers, parallel work and work in progress are visible and managed.
4. Technical risks, delays and cross-module changes have a decision and escalation route.

2 looks like: issues and milestones that match the roadmap; a board with limited work in progress; resolved decision issues.
Failure signals: many tasks open, few done; agents waiting on each other; new requests constantly interrupt the plan; nobody knows what blocks the project.
Split tasks so each can be accepted and integrated on its own, not by file count.

## 11 Documentation and knowledge
Can the project continue without its author's memory?

1. The README explains the goal, how to run it, how to test it, and the main entry points.
2. Architecture documents match the code, and key decisions are recorded (ADRs).
3. Public interfaces, configuration, environment requirements and routine operations are documented.
4. A new developer or agent can safely complete an ordinary task without verbal explanation.

2 looks like: docs updated in the same PRs as code; a passed handover test (stress test 6).
Failure signals: forgotten design reasons; every new agent re-reads the whole codebase; docs that contradict the code.

## 12 Engineering metrics and continuous improvement
Can the project show that it is getting better, not just bigger?

1. Change lead time (commit to delivery) and delivery frequency are measured.
2. Change failure rate, recovery time and rework caused by failures are recorded.
3. Flaky tests, waiting, rework and recurring bottlenecks are identified.
4. Trends are reviewed regularly, and process or architecture changes follow the evidence.

2 looks like: DORA-style figures from Git and GitHub (lead time, frequency, failure and rework rates), tracked across audits. Compare the project with its own past, not with other projects.
Failure signals: no data; the same problems recur in every phase.

## Totals

- 48 checks × 2 = 96 points, minus not-applicable checks.
- Report the overall percentage, each dimension's x/8, and the trend since the previous audit.
- Band labels for orientation only:
  - below 34 %: initial;
  - 34–66 %: developing;
  - 67–84 %: established;
  - 85 % and above: mature.
