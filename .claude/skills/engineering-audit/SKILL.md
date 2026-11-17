---
name: engineering-audit
description: Run the Research Multiplier engineering maturity audit. Score 12 dimensions × 4 checks (0/1/2) from evidence in this repository, run or record the six stress tests, classify gaps as P0/P1/P2 against docs/roadmap.md, and write the result to docs/audits/ with a comparison to the previous audit. Use when asked for an engineering audit, maturity check, health check, 工程审计, 工程成熟度自查, or at the end of a roadmap phase.
---

# Engineering maturity audit

The audit answers one question: **when the project doubles in complexity, will the cost of changing it stay under control?** It measures six capabilities — understandable, modifiable, verifiable, deliverable, recoverable, sustainable — through 48 checks.

Files in this skill:

- [references/checklist.md](references/checklist.md): the 12 dimensions, their 4 checks each, and what earns 0, 1 or 2.
- [references/evidence.md](references/evidence.md): where to find evidence in this repository, and the commands that produce it.
- [references/stress-tests.md](references/stress-tests.md): the six practical tests and how to run them here.
- [references/report-template.md](references/report-template.md): the structure of an audit record.

## Scoring discipline

These rules matter more than the checklist:

1. **Score what exists and has been exercised, never what could be done.**
   - 2: a working mechanism with evidence that it ran, such as a CI run, a passing test, or a merged PR that followed the rule.
   - 1: partial, inconsistent, or written down but not yet exercised. A rule added to a document this month scores 1 until something shows it working.
   - 0: absent, or handled ad hoc.
2. **Every score cites evidence:** a file path, a command and its result, a CI run, or a PR. Run the commands in `evidence.md`; do not infer from memory or from earlier audits.
3. **Not applicable** items leave the denominator, with one sentence saying why.
4. **Compare, don't restate.** Read the previous record in `docs/audits/`, and explain every score that changed and why.
5. **The audit is read-only.**
   - The only files it writes are the audit record and its Chinese version.
   - Fixes become roadmap tasks or issues, not edits made during the audit.
   - Stress tests that modify code run in a temporary worktree and are never committed.
6. **Never print secrets.** Report a suspected leak by file path and line, never the value.

## Procedure

1. **Baseline.** Open the newest file in `docs/audits/` and note its scores and open actions.
2. **Evidence.** Work through `references/evidence.md` dimension by dimension. Run the listed commands from the repository root. If one cannot run (no network, no database), record that and score from what you can verify.
3. **Score.** Apply `references/checklist.md` to all 48 checks, citing evidence for each.
4. **Stress tests.** Follow `references/stress-tests.md`.
   - Run tests 1, 4 and 5 when the environment allows.
   - Ask the user before tests 3 and 6 (they start agents or take long) and before anything that needs network access or a database the audit did not create.
   - Mark tests that were not run as *not run*, never as passed.
5. **Classify gaps.**
   - **P0**: threatens continuity. Examples: builds that cannot be reproduced, key changes without reliable tests, unverified data recovery, leaked credentials, no way to recover a release, unowned core areas.
   - **P1**: slows iteration badly. Examples: tight coupling, unstable public interfaces, no review, unclear task dependencies, agents duplicating work, stale documents.
   - **P2**: incremental improvement.

   Map each gap to an existing roadmap task, or propose a new one with acceptance criteria.
6. **Record.**
   - Write `docs/audits/YYYY-MM.md` (English) and `docs/audits/YYYY-MM.zh-CN.md` (Chinese) following `references/report-template.md`.
   - If a record for the month already exists, add a suffix such as `YYYY-MM-b`.
   - Add the new record to the index in `docs/audits/README.md`.
7. **Report to the user**, in the language they used:
   - the total and the percentage;
   - the dimensions that changed;
   - the stress-test results;
   - the three highest-priority actions.

   Put the full detail in the record, not in chat.

## Interpreting results

- Do not chase the total. A project with high documentation and process scores but weak tests (05) or recovery (03, 08) is not mature.
- Weight these five dimensions most for this project: 02 architecture, 05 testing, 06 version control and collaboration, 07 CI/CD, and 10 project and task management.
- Treat change impact radius as the key architecture measure: the number of modules a typical feature has to touch.
- Research reproducibility counts as recovery. Any historic result must be reproducible from the code version, parameters, dependencies and data.
