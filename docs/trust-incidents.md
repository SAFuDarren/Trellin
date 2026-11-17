# Report and contain verification incidents

An incident report links a concrete reproduction to the exact revision of a checker. When an execution is available, give its verification job ID so the database uses the revision recorded for that job. You can also report a workflow block on its own job.

Final Lean checks that use scoped local lemmas record both the base Lean checker and the local checker. A job-based incident defaults to the local checker. Use `--checker REVISION` to identify the affected checker explicitly. The database accepts either checker recorded on that job. With no job ID, the specified revision must already appear in a verification. The review packet lists the local revision under `effective_checker_revision` and the base under `checker_revision`.

```sh
research-multiplier trust report checker-reproduction.md --kind verifier_vulnerability \
  --job JOB_ID --summary "The checker accepted this out-of-scope candidate"
research-multiplier trust status
research-multiplier trust status REPORT_ID
```

The report file is stored by content hash in the workspace artifact store. Researchers and verifiers can report checker vulnerabilities. The scheduler can report workflow blocks, and an agent can report workflow blocks for jobs to which it submitted an attempt. Use `--role` to select an authorized configured login.

A researcher can contain an incident by retiring the exact revision listed on the report. The decision is recorded together with retirement. The existing evidence dependencies mark affected receipts and downstream results for recheck.

```sh
research-multiplier trust decide REPORT_ID contain --actor Researcher --reason "Reproduced the incorrect acceptance"
```

Use `dismiss` with an explanation to close an incident that needs no checker retirement.

## Compare verification observations

```sh
research-multiplier trust scan --from 2026-11-01T00:00:00Z --to 2026-11-08T00:00:00Z
```

The scan compares timestamped attempts with the adjacent preceding window of equal length, grouped by exact checker revision. It includes counts of successful and failed checks, verified receipts with a scope difference, and the review, recheck and incident state visible at scan time. Containment is a separate researcher decision. A change in the counts can reflect a change in which tasks ran.

Each check and receipt contributes once. Scoped final Lean checks count toward their local checker revision. An active hold or containment for the base checker also applies to scoped checks that depend on it.

## Apply an approved monitoring policy

Record the minimum observations required in each window and an absolute increase in percentage points. A verifier process can run the existing observation query through the policy. A matching signal records a hold that blocks new verification leases, receipts and claim promotions based on the affected revision. The review inbox shows the hold. Release it after reviewing the task mix, or use incident containment to retire the revision and mark its past receipts for recheck.

```sh
research-multiplier trust policy --min-attempts 30 --jump-percent 25 \
  --actor Researcher --reason "Threshold for investigating changes in checker behavior"
research-multiplier trust monitor --from WINDOW_START --to WINDOW_END
research-multiplier trust holds
research-multiplier trust release HOLD_ID --actor Researcher --reason "Reviewed the observations"
```

An operator can schedule the monitor command in a verifier service. The program signs no new mathematical results based on these statistics.

## Sample adopted evidence and keep incident regressions

Use a date range and seed to select current adopted receipts for an independent
deep review. Repeat the same selection against the same evidence state with the
same seed.

```sh
research-multiplier trust sample --from WINDOW_START --to WINDOW_END --seed REVIEW_SEED --limit 5
research-multiplier review packet EVIDENCE_ID
```

After adding a regression, link its case and exact fixture to the incident.
The human decision retains the fixture by content hash. Run the actual test
through the usual suite.

```sh
research-multiplier trust regression INCIDENT_ID repro.json --test-case MODULE.CLASS.TEST \
  --actor Researcher --reason "Keep the formerly accepted proof as a failing test"
research-multiplier trust regressions INCIDENT_ID
```

## Replay a structured Lean verification incident

If an incident names a verification job that rejected a structured Lean term,
capture that exact candidate as a development case. Use `--expect rejected`
for an invalid proof and `--expect verified` for a valid proof independently
established against the same frozen statement. The researcher records the
expected outcome, and the case records the original attempt and checker, the
statement, the exact proof inputs and their content hashes.

```sh
research-multiplier trust lean-regression INCIDENT_ID --expect verified \
  --actor Researcher --reason "The independently checked proof matches the frozen scope"
research-multiplier research backup-project --out BACKUP_DIRECTORY
research-multiplier research restore-project BACKUP_DIRECTORY SCRATCH_WORKSPACE
research-multiplier --workspace SCRATCH_WORKSPACE trust replay-lean-regression REGRESSION_ID
```

Back up after capturing the case. Before restoring, select a fresh PostgreSQL
database through `RM_MIGRATION_DSN`. Before replaying, point the human,
scheduler and verifier connections at that restored database. Select the
reviewed Lean runtime with `--lean` or the compiled project with
`--mathlib-project` when needed.

Each replay runs an independent verification attempt with no model calls. It
writes its result under the restored workspace's private evaluation archive.
The command returns exit code `1` when the observed result differs from the
reviewed expectation or when verification is inconclusive. A verified result
creates a pending receipt in the restored workspace. The original incident,
claim and receipts are preserved. Executable cases cover structured goal
and refutation terms. Reports about other inputs or infrastructure can still
use `trust regression` to link a separate reproduction.

For a local periodic job, use `trust monitor --last-hours 24` with only the
verifier login. The approved policy still controls holds. Active holds are
available in the review inbox.

## Observe Lean re-evaluation triggers and run the development queue

From an initialized project with the verifier connection, run a local scan.
A scan records the installed implementation on first observation, a monthly
UTC review, recorded workflow blocks, recorded model identities when first
observed, and claims that reached the model budget on at least three recorded
passes in the current month. It queues each executable incident fixture at
most once per exact installed implementation. A scan with `--limit`
processes at most that many new executable fixtures. Additional scans pick up
the remaining captures and any newly observed signals.

```sh
research-multiplier trust lean-recheck-scan
research-multiplier trust lean-recheck-status
```

Optionally supply `--campaign REPORT_SHA256` to observe a completed campaign
on an unchanged sealed development corpus. A candidate that does not increase
the exact-result count compared with a distinct recorded candidate under the
same corpus, baseline and budget creates a benchmark review signal. The report
digest and both counts identify the observation. Holdout reports stay in the
separate campaign and approval workflow.

Back up after scanning and restore the backup to a separate project and fresh
PostgreSQL database as above. Point all three role connections at the restored
database before running the worker with the scanned installation. The worker
claims one executable replay at a time and records a content-addressed report.
An interrupted acknowledgement can recover the same completed verification.
An inconclusive run stays visible and can be retried after fixing the runtime
or archive with `--retry-inconclusive`. `--limit` permits a bounded batch.

```sh
research-multiplier --workspace SCRATCH_WORKSPACE trust lean-recheck-work
research-multiplier --workspace SCRATCH_WORKSPACE trust lean-recheck-status
```

The scanner and worker reserve zero model calls. Review signals surface work
for evaluation. Route or checker changes still use the existing human approval
and containment commands. The local scan can run when requested or through an
existing external operator schedule. Observed model identities come from
recorded local runs.
