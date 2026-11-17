# Compare versions against reviewed cases

Use a development set to diagnose changes and hold back a separate set for decisions. Case inputs carry a deterministic digest so an unchanged case cannot move into the holdout under a different ID. Retain semantically distinct cases when building the sets.

The current evaluator checks two recorded response sets against the same frozen inputs with the registered exact domain verifiers. It records a dry comparison in the current research workspace without making model calls.

## Cases and responder records

Supply two JSON documents with `format_version: 1` and a `cases` array. A case has a unique `id`, a registered production `domain`, its exact domain `input`, and a reviewed `group`. The groups are:

- `known_theorem`, which expects verification of the frozen claim
- `false_theorem`, which expects a checked refutation
- `scope_drift`, which expects refusal of a submission bound to another input
- `invalid_proof`, which expects refusal of an invalid certificate

This is a one-case development document for maximum flow.

```json
{
  "format_version": 1,
  "cases": [{
    "id": "dev-flow-two",
    "domain": "fixed-directed-max-flow",
    "input": {
      "vertices": ["s", "t"],
      "source": "s",
      "sink": "t",
      "edges": [["s", "t", 2]],
      "target_value": 2
    },
    "group": "known_theorem"
  }]
}
```

Run `research-multiplier eval keys CASES_JSON` to get the exact input digests. Each responder document has `format_version: 1`, a `version` object with `harness`, `model`, and `revision`, and a `responses` object keyed by case ID. Use `{"action": "abstain"}`, `{"action": "reject"}`, or `{"action": "submit", "subject_sha256": "HEX", "certificate": {...}}`. Omitted case IDs count as abstentions. A submission to the wrong digest or with an invalid certificate is rejected by the evaluator. A conclusive result that contradicts its reviewed label is reported as a checked conflict.

## Collect a program or model response set

```sh
research-multiplier eval collect DEVELOPMENT HOLDOUT --split development --responder program
research-multiplier eval collect DEVELOPMENT HOLDOUT --split development --responder model \
  --confirm-public --max-cases 32 --timeout 60 --max-output-tokens 4096
```

Use the returned `responses_path` as the baseline or candidate input to `eval compare`. The program collector invokes a registered candidate generator and abstains where that domain requires a supplied witness. The model collector uses the configured `PROVER` adapter and sends one canonical public claim and its digest per case. Review the inputs before using `--confirm-public`. Case IDs, group labels, other cases, and evaluator feedback stay in the evaluation workspace. Use a trusted adapter or an independently provisioned OS account for untrusted code.

The collector records the split inputs, exact response file, implementation identity, timings, token use when reported, typed failures, and model transcripts in the private evaluation archive. Each completed case is saved as the collection runs. The response file is published once collection finishes. A malformed model reply contributes an abstention. A well-formed submission with a changed digest remains available for the independent scope check.

These commands collect certificate-generation behavior for the supplied claims. To measure resistance to scope drift and invalid certificates with injected submissions, provide separately produced response records for those cases. The model collection sends the original frozen claim for every group.

## Exercise the checker with controlled faults

Keep development and holdout datasets in a separate evaluation workspace. Supply their paths explicitly and run the fault groups separately.

```sh
research-multiplier eval inject "/path/to/evaluation/development.json"
research-multiplier eval inject "/path/to/evaluation/holdout.json"
```

The program first verifies the original witness. For a scope fault it changes
the flow target, color bound or graph constraint and constructs a checked witness for the modified
contract. For a witness fault it keeps the original contract and damages a flow
or introduces a coloring collision. The command evaluates each submission using
the regular evaluation gate and returns a failing exit code if the intended
check does not reject it. The inputs, injected submissions, checker revisions,
and per-case reasons are archived in the selected workspace. These are public
regression examples. Use separately controlled, unseen cases to evaluate an
experimental model.

## Compare archived responses

```sh
research-multiplier eval compare DEVELOPMENT HOLDOUT BASELINE CANDIDATE --split development
research-multiplier eval compare DEVELOPMENT HOLDOUT BASELINE_HOLDOUT CANDIDATE_HOLDOUT --split holdout --record
research-multiplier eval reproduce REPORT_SHA256
```

`--record` registers the immutable comparison summary through the database's verifier role. The input files, case-level details and checker identities are kept as checked archives under the workspace's `evaluation/private/objects`. Keep that directory with the private evaluation backup. Use an independently provisioned OS account when collecting responses from untrusted workers. This local replay command does not run those workers.

## Researcher decision

A holdout report can be recommended when it covers all four groups and at least ten cases, matches more expected outcomes than the baseline, has no checked conflicts, and does not regress on scope drift or rejected injections. Inspect the evidence before recording your decision.

```sh
research-multiplier eval status REPORT_ID
research-multiplier eval approve REPORT_ID --hash REPORT_SHA256 --actor "your name" \
  --reason "Reviewed the sealed holdout and candidate version"
```

The human approval is a recommendation bound to the exact archived report and version identities. It does not change the live harness or routing table. The reported counts measure behavior on the supplied response records and cases. Operational error escape rates, reviewer effort, and production shadow rollout require live independent measurement.

## Compare one component

Use the same cases and recorded model ID for both response sets. In a challenger comparison, change or add one declared component. In an ablation, remove one. Keep the model revision and all other settings fixed. Save the shared settings and provide their digest inside the manifest.

The response digests are `responses.sha256` from `eval collect`. For a separately prepared response document or a settings document, compute the canonical JSON digest with the installed package.

```sh
python3 -c 'import json, sys; from research_multiplier.serialization import fingerprint; print(fingerprint(json.load(open(sys.argv[1], encoding="utf-8"))))' RESPONSES_JSON
```

This manifest compares an existing context component with a revised one. Replace the digest fields with the hashes of the supplied documents.

```json
{
  "format_version": 1,
  "kind": "challenger",
  "controls": {
    "model_revision": "reviewed-model-revision",
    "settings_sha256": "SHARED_SETTINGS_SHA256"
  },
  "baseline": {
    "response_sha256": "BASELINE_RESPONSE_SHA256",
    "components": {"proof-context": "1", "review-summary": "1"}
  },
  "candidate": {
    "response_sha256": "CANDIDATE_RESPONSE_SHA256",
    "components": {"proof-context": "2", "review-summary": "1"}
  }
}
```

```sh
research-multiplier eval compare DEVELOPMENT HOLDOUT BASELINE CANDIDATE --component change.json --split holdout --record
research-multiplier eval reproduce REPORT_SHA256
research-multiplier eval approve REPORT_ID --hash REPORT_SHA256 --actor Researcher --reason "Reviewed the paired outcomes and trust checks"
```

The archived report binds the exact response documents, shared declarations and original checker results. It shows the number of cases each side alone matched, both matched or both missed for every group. The standard holdout gate still requires improved outcomes without a regression in measured scope drift or rejection counts and without checked conflicts. The report records no recommendation when an ablation ties on the available cases. Use the returned top-level artifact hash to reproduce or approve the complete comparison.

This runs against archived responses. It checks the declared component difference and the measured outcomes. Independently reviewing the settings and ensuring those controls held at collection time remain part of the trial. Actual error escape, reviewer effort, runtime savings and production behavior require live measurement.

## Trial and choose a production prover profile

The fixed-network production prover can run a paired trial on a sealed
holdout. The baseline is the profile currently selected for this workspace.
Set the proposed profile through `RESEARCH_MULTIPLIER_PROFILE_<NAME>_` followed
by the existing provider, model, family and transport setting names. Keep the
default `PROVER` profile available for rollback. The current independent
`AUDITOR` applies the same scope review to both sides. The independent
certificate check and deterministic refutation paths are also shared.
Scopes, phases and proof thresholds stay under the existing verifier.

```sh
research-multiplier eval shadow DEVELOPMENT HOLDOUT --candidate-profile CHALLENGER --confirm-public
research-multiplier eval shadow-status EXPERIMENT_UUID
research-multiplier eval shadow-live CLAIM_UUID --candidate-profile CHALLENGER --confirm-public
research-multiplier eval shadow-live-status LIVE_TRIAL_UUID
```

Both runs see the same sealed public inputs. Case labels, expected results,
other cases and checks stay in the evaluation archive. Scope and certificate
probes exercise the checker directly on the same bad input for both sides.
Use the returned experiment to read elapsed time, reported cost, model-call
counts, and paired outcomes. A missing price or review duration is unmeasured.
The trial writes evaluation records and model-budget usage. Ordinary proof
evidence and result status continue to come from the production workflow.

The opt-in live trial reads the exact frozen public claim and current
approved proof context from PostgreSQL. You can supply up to 32 claim IDs.
It uses the same independent checker and auditor. Previously adopted,
current exact results provide reviewed labels. The report leaves unknown
outcomes unlabelled. A live trial observes the specified tasks when called,
so the production queue and existing human decisions can proceed during that
work. A project with a model budget also needs a tariff for the challenger
profile, applied through its ordinary budget controls.

To include measured review effort, log the time spent inspecting each side of
the same case and use the same review protocol on both sides.

```sh
research-multiplier eval shadow-review EXPERIMENT_UUID case-1 baseline --minutes 3 --actor Researcher
research-multiplier eval shadow-review EXPERIMENT_UUID case-1 candidate --minutes 2 --actor Researcher
research-multiplier eval approve EVALUATION_REPORT_UUID --hash EVALUATION_HASH --actor Researcher --reason "Reviewed the holdout"
research-multiplier eval activate EXPERIMENT_UUID --hash EXPERIMENT_HASH \
  --live-trial LIVE_TRIAL_UUID --live-hash LIVE_TRIAL_HASH --actor Researcher --reason "Adopt the verified profile"
research-multiplier eval rollback ACTIVATION_EVENT_UUID --actor Researcher --reason "Restore the previous profile"
```

Activation binds the exact profiles, current baseline, independent auditor,
and production implementation tested in both records. It requires the
standard holdout gate, an eligible live trial, unchanged or improved
semantic scope drift counts, and review minutes at or below baseline over
the measured pairs. The live candidate must produce at least one conclusive
result, match or exceed baseline conclusiveness, and avoid conflicts with
a reviewed label. Enter both sides of every
measured pair before promotion. Production workers load the authorized profile
on their next model job and validate its recorded configuration hash. Keep
approval-scoped profiles available in the local environment. Rollback opens
the previously recorded exact profile for new work. Previous verification
receipts retain their original model provenance.

This route evolves the public fixed-network prover profile. Extending paired
experiments to Lean tactic search, arbitrary planners, or a new scientific
domain requires a matching runner and verifier. Both benchmark and live
trials are explicitly selected jobs. Continuously mirroring live queues is a
separate scheduling capability. Changes to model weights at a provider are
measurable when the provider returns a stable revision. Prefer versioned model
IDs for reproducible trials.

## Compare Lean search on a public statement

Start with an open public Lean statement whose exact formalization and
basis have been reviewed. Before beginning proof work on that statement,
take a paired [project backup](research-foundation.md#recover-and-monitor).
Restore the same archive twice, each time into a new workspace directory
and a fresh PostgreSQL database. Set `RM_MIGRATION_DSN` to the owner of
the intended database for each backup or restore command.

Use the source project's verifier login for `RM_VERIFIER_DSN`. Set the
four `RM_SHADOW_BASELINE_{HUMAN,SCHEDULER,AGENT,VERIFIER}_DSN` variables
to the corresponding logins for the baseline copy, and the four
`RM_SHADOW_CANDIDATE_{HUMAN,SCHEDULER,AGENT,VERIFIER}_DSN` variables for
the candidate copy. Keep these copies separate from active research work.

```sh
research-multiplier --workspace SOURCE eval shadow-lean STATEMENT_ID BASELINE CANDIDATE \
  --baseline-strategy legacy --candidate-strategy ranked --confirm-public --rounds 2
research-multiplier --workspace SOURCE eval shadow-lean-status REPORT_SHA256
```

To compare model profiles under the same search strategy, use
`--candidate-profile CHALLENGER` and leave both strategies at their default.
Use `--baseline-profile BASELINE` when the baseline is a named profile.
Configure the profiles as described in the
[adapter guide](model-adapters.md). If the approved statement uses Mathlib,
select its reviewed local toolchain and package with `--lean` and
`--mathlib-project` as needed. Use `--rounds 0` with distinct strategies
for a deterministic pair without a model configuration.

The command checks the frozen statement, basis, Lean environment, effective
adapter settings and starting budget before either side runs. It fingerprints
the public working-lemma and published-theorem corpus, including every reachable
content-addressed proof artifact, and checks it against the source project.
The implementation digest covers the installed Python modules, SQL migrations,
Lean support files and schemas. Each side runs the
regular Lean worker with its own round and time limits, so two rounds can
reserve up to four model calls total. Existing project model tariffs and limits
apply separately to each copy. The report records committed search passes,
exact proof or refutation receipts, unresolved searches, elapsed time, and
provider-reported token usage and charges. Missing provider measurements
remain unavailable. The source database is used for readback and the
comparison and exact receipt artifacts are saved under the source workspace's
private evaluation directory. Shadow receipts stay pending review.

### Compare separate installed builds

Install each trusted Research Multiplier wheel into a separate directory with
`python -m pip install --no-deps --target INSTALL_DIR WHEEL`. Use the evaluator
from an installation whose migration history contains both selected builds.
Migrate the source through the evaluator's current schema, then restore the
same backup into both shadow workspaces. Both sides use that same schema.

```sh
research-multiplier --workspace SOURCE eval shadow-lean STATEMENT_ID BASELINE CANDIDATE \
  --baseline-install INSTALL_A --candidate-install INSTALL_B --rounds 0
```

The same installation options work with `eval shadow-lean-campaign` and
its total call allowance. A build comparison keeps the strategy and effective
model configuration fixed. The evaluator copies and checks the recorded package
files into temporary directories, launches each selected implementation with the
same Python interpreter, and records both installed versions, checksums and
migration heads. Each worker must independently confirm the scope, starting
proof corpus, reviewed Lean environment and budget in its restored database.

Build reports document the effect of switching the selected application bundle.
Use builds that differ in a single reviewed component to interpret its effect.
The report stays available by hash and cannot activate a production route. Software
upgrade and route approval retain their separate review steps. Worker-reported
metrics can change definition between builds. Compare their definitions before
using those fields to rank a change.

### Run ordered cases against a shared budget

For multi-claim trials, prepare a manifest with separate restored pairs for
each case. A case can contain multiple statements in execution order. Every
statement needs its own confirmed scope before taking the source backup. Each
side continues in the same isolated workspace throughout its case, so later
steps can reuse the checked results of earlier steps. The evaluator checks
that the reusable corpus at each step agrees with its recorded predecessor.
Choose a development manifest for tuning and a separately reviewed holdout
manifest for activation. Seal both inputs in the evaluation workspace before
any proof work. The corpus JSON has `format_version: 1` and `development`
and `holdout` arrays. Each row has `statement_id` and `expected`. Both splits
use current, reviewed public statements. The Lean kernel encoding ignores
binder names while retaining the scoped proposition and the meaning of its
constants. The workspace keeps each measured identity in its first reviewed
split even across later corpus versions.

```sh
research-multiplier --workspace SOURCE eval lean-corpus-seal corpus.json \
  --actor Researcher --reason "Review disjoint development and holdout inputs"
```

For a promotion-eligible campaign pass `--corpus-sha256` with the returned
artifact digest and run exactly the reviewed statements and labels from the
chosen split. A sealed strategy comparison needs at least ten distinct Lean
development statements. A paired run without a seal is diagnostic. Keep the
evaluation workspace and its snapshots under the control of the evaluator.

```json
{
  "format_version": 1,
  "split": "holdout",
  "cases": [
    {
      "id": "reuse-chain",
      "baseline_workspace": "/path/to/chain-baseline",
      "candidate_workspace": "/path/to/chain-candidate",
      "statements": ["00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"],
      "expected": ["verified", "verified"]
    },
    {
      "id": "refutation",
      "baseline_workspace": "/path/to/refutation-baseline",
      "candidate_workspace": "/path/to/refutation-candidate",
      "statements": ["00000000-0000-0000-0000-000000000003"],
      "expected": ["refuted"]
    }
  ]
}
```

Use the exact statement UUIDs and assign one expected outcome per step.
Choose `open` for a control expected to remain unresolved. Relative
workspace paths resolve from the manifest directory. Connect the four scratch
roles separately for each side and case. For `reuse-chain`, use variables
such as `RM_SHADOW_REUSE_CHAIN_BASELINE_HUMAN_DSN` and
`RM_SHADOW_REUSE_CHAIN_CANDIDATE_VERIFIER_DSN`.

```sh
research-multiplier --workspace SOURCE eval shadow-lean-campaign holdout.json \
  --baseline-strategy legacy --candidate-strategy ranked --rounds 0
research-multiplier --workspace SOURCE eval shadow-lean-campaign holdout.json \
  --baseline-strategy legacy --candidate-strategy ranked --confirm-public \
  --rounds 2 --max-model-calls 12 --campaign-timeout 900 --corpus-sha256 CORPUS_SHA256
```

The call ceiling must cover two sides times the number of statements times
the requested rounds. Both sides finish preflight before any case can run.
The side that starts alternates across cases and across steps within a case.
Each report records the exact order, per-step results, failed or unmeasured
runs, known model usage and aggregate wall time. Campaigns compare strategies
or profiles available in the installed implementation.

### Measure one Lean scaffolding component

Use a sealed corpus with at least ten distinct reviewed Lean development
statements and supply the complete selected split. The comparison holds the
installed package, ranked strategy, model profile, proof corpus and limits
fixed. For `cross-claim-working-reuse`, the candidate omits only discovery and
replay of working proofs from other claims. Both sides retain in-claim lemmas,
Lean verification, scope checks, budgets and the ordinary evidence review.

```sh
research-multiplier --workspace SOURCE eval shadow-lean-campaign manifest.json \
  --corpus-sha256 CORPUS_SHA256 --ablate cross-claim-working-reuse --rounds 0
```

The report pins the component version and actual switch on each side. It counts
paired outcomes for both sides, each side alone and unavailable pairs. It also
records wall time, reserved model calls, provider-reported usage and archived
proof artifacts. Use a corpus that exercises the component to interpret those
differences. The report is diagnostic evidence for a reviewed software change.
Production route activation uses a separate eligible holdout report.

## Review and activate a measured Lean change

The public signed worker reads an active Lean strategy and author from its
workspace. The default strategy is `ranked`. To compare against an active named
profile, pass its exact settings with `--baseline-profile` and
`--baseline-strategy`. A comparison does not change either production route.

Use a sealed holdout campaign with both execution orders and reviewed proof and
refutation cases. For each case, record the minutes spent reviewing each side
and the number of invalid receipts found. Inspect the archived proof and
refutation reports before recording the review.

```json
{
  "format_version": 1,
  "cases": [{
    "case_id": "public-theorem",
    "baseline_minutes": "3.1",
    "candidate_minutes": "2.5",
    "baseline_invalid_receipts": 0,
    "candidate_invalid_receipts": 0
  }]
}
```

Include every case. The program checks the campaign artifact, starting-source
identity, execution implementation and effective model configuration. It checks
that existing exact results carry forward, review time does not increase and
the proposed route improves the number of exact results or measured effort.
A zero-model campaign provides program-level evidence. The production model
activation also needs a compared model configuration.

```sh
research-multiplier eval lean-active
research-multiplier eval lean-activate REPORT_SHA256 REVIEW.json --actor Researcher --reason "Reviewed matched holdout results"
research-multiplier eval lean-rollback EVENT_UUID --actor Researcher --reason "Restore the previous measured route"
```

Activation uses verifier and human connections. The verifier records the checked
campaign, review and settings before the human connection applies the route.
Rollback uses the human connection. The database protects against simultaneous
changes and records the reviewed version, profile and reason.
Worker passes record the route event used. Existing restricted inference
policies use their explicitly approved private settings.

`eval lean-active` also shows recent worker pass counts for the active event
and whether the currently installed implementation still matches that event.

### Resume the default after an implementation upgrade

When the installed implementation changes, the earlier Lean route remains
recorded with its original implementation. Restore that implementation to keep
using the route, or authorize the installed version's default `ranked` strategy.
Use the current event ID printed by `eval lean-active`.

```sh
research-multiplier eval lean-reset-after-upgrade ACTIVE_EVENT_UUID \
  --actor Researcher --reason "Authorize the installed default on the upgraded version"
```

An upgrade reset records the earlier route and installed version. The worker
resumes with the default program-first strategy and loads the workspace's
default Lean author only when an approved run needs it. A later activation
requires a new paired campaign against that installed default. A rollback of
the later activation restores the exact baseline measured in that campaign.
