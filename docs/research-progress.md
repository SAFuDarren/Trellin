# Advance an approved research goal

Run `research-multiplier research resume` when returning to a workspace. It shows open decisions and recent recorded progress first. Add a claim ID to focus the view, or `--details` for exact references and hashes. The command reads existing project state and works without a model call.

A progress policy binds an already frozen claim and formalization packet, the
metric, allowed actions, priority and maximum work budget. The researcher
reviews and approves its exact hash. A worker never alters the claim or signs
a review decision.

```json
{
  "packet_id": "FORMALIZATION_PACKET_UUID",
  "metric": "flow-interval-width",
  "actions": ["prove", "await-review", "snapshot"],
  "proof_timeout_seconds": 60,
  "proof_max_attempts": 1,
  "max_rounds": 2,
  "budget_seconds": 124,
  "priority": 10
}
```

```sh
research-multiplier advance route FORMALIZATION_PACKET_UUID
research-multiplier advance propose policy.json
research-multiplier advance approve POLICY_UUID --hash POLICY_SHA256 --actor Researcher --reason 'Exact scope and budget checked'
research-multiplier advance worker
research-multiplier advance status POLICY_UUID
```

The route command suggests a policy from the frozen packet and the
registered domain capabilities. An available deterministic solver takes the
proof route. A checker that needs a supplied witness uses observation
mode. An unavailable packet or stale decisive result reports the relevant
human decision to make. Use `advance propose --for-packet PACKET_ID` to
register the recommended policy with bounded defaults, or supply a policy
file to set the budget explicitly. Both proposals require approval of
their exact hash before a worker can execute them.

The worker selects eligible policies in priority order, registers the exact
approved steps, and executes ready work across policies in the project
database. Use `advance worker --policy FIRST --policy SECOND --parallel 2 --watch 5`
to limit a worker to specified policies. The same scope applies to verifier
observations in `--stage verifier`. Once the
researcher approves or returns a receipt using the normal review commands,
this worker continues the affected plan on its next pass. Pending review
holds no execution slot. A returned receipt, exhausted budget, failed proof,
inconsistent receipts, or an unavailable scope has an explicit state. The
status includes the last route and branch failure details. An unchanged
returned or exhausted route produces no further attempts on subsequent passes.

A policy with `actions: []` observes evidence already produced by an
independent checker, including matching Lean receipts. `exact-closure` counts
only current, exact and researcher-approved evidence for the same frozen
scope. A refutation is recorded as such. `flow-interval-width` starts with
zero flow as the lower bound and the capacity leaving the source as the upper
bound. This metric advances when an approved checked flow and cut agree on the
optimum. The verifier reads these values from the actual receipt. Callers do
not submit scores. Existing proof artifacts, plan archives and evidence IDs
carry their original scope. Researcher approval of a receipt and formal
promotion of the claim remain separate decisions.

If a prior proof becomes stale while the claim is open, a new round can be
scheduled within the approved limit. A closed claim with stale decisive
evidence needs an explicit `research reopen` decision before further work.
Recording a new round does not extend the original total budget. Stop the
policy with `advance stop POLICY_UUID --reason 'End this investigation'`.

`advance worker --stage verifier` records observations with only the verifier
login. `--stage scheduler` drives approved routes and sends only scheduler,
agent and verifier credentials to the called deterministic proof process.
A worker needs no human login or model key. The process imports the program
from its own installation directory, so a research file cannot replace the
program through the working directory. The subprocess environment allowlist
is distinct from an operating-system sandbox. Use your project database and
the same active workspace for each role.

Automatic proof uses domains with a separately registered deterministic solver.
The built-in solvers cover fixed-network maximum flow and finite graph coloring.
Domains whose verifiers require a supplied certificate can still use observation
mode.

## Advance a confirmed Lean statement

Request the recommended policy for a packet with a confirmed Lean statement,
then approve its hash. The default policy requires no model. The worker tries
ready reductions and applicable, already checked native theorems. For a Lake
scope pass the existing project path to the worker through `--mathlib-project`
or `RM_LEAN_MATHLIB_PROJECT`.

```sh
research-multiplier advance propose --for-packet PACKET_UUID
research-multiplier advance approve POLICY_UUID --hash POLICY_HASH --actor Researcher --reason "Use the confirmed Lean scope"
research-multiplier advance worker --policy POLICY_UUID
research-multiplier research resume
```

For a public scope, `advance propose --for-packet PACKET_UUID --lean-model-rounds N`
authorizes up to N proposal rounds for the current confirmed inquiry, within the
goal-wide Lean model allowance. Use the combined worker stage to run Lean work.
Each pass can send one proposal after trying deterministic proofs and supported
finite counterexamples. More passes can continue from the same archived route,
working lemmas and reasons for failure. Use `advance worker --policy POLICY_UUID
--watch 5` to keep advancing, or run a single pass and return later.
`advance status POLICY_UUID` includes the recorded passes. Run
`advance metrics POLICY_UUID --details` to see completed worker passes, recorded
worker time, current researcher decisions, and model reservations for the same
frozen goal across all of its policies. Compare elapsed wall time separately
when running workers in parallel. Model timing, input/output tokens, cached
input tokens, and dollar cost each show how many calls reported that field.
A missing provider amount stays unknown. Human time reflects only explicitly
logged entries.

Checked working lemmas
and reductions can advance the inquiry without refilling its signed allowance.

When the model reports a blocked route, the next step can try a different method,
work on another route's checked implication or seek an exact refutation.
For supported conjunctions the worker can first build a checked plan under
the original explicit binders and premises. It compares eligible routes by
their verified and remaining obligations and their stalled attempts. Finite
Boolean relations and bounded natural-number cases can produce refutation
candidates. An exact Lean proof of the full frozen statement's negation is
required for a refutation receipt.
The worker can resume after a verified input change or an allowance change.
Existing evidence review and adoption apply to a complete proof or exact
refutation. Unfinished proof obligations remain route results. Measured changes
to the meaning of the reviewed Lean scope appear in the review inbox and
`research resume`.

You can map different confirmed statements to different Lake projects with
`--lake-map FILE`. The JSON object maps Lean statement UUIDs to project folders.
Relative paths are relative to the mapping file. `--mathlib-project` supplies
the default for this worker's Lake statements. A running worker caches the
tracked compiled import closure and wakes affected policies when those files
change. The next verification still measures the exact input contents and the
meaning of the reviewed scope.

`advance worker --watch 5` polls at five-second intervals. If the database
temporarily disconnects, it reconnects with bounded backoff. PostgreSQL
associates each recorded Lean run with the worker session that took it. Permanent
configuration and permission errors still surface in the command. If a Lean
verification lease expires, the watching worker retries the same saved candidate
under a current lease. The retry uses no additional model call for that
candidate. A single-pass worker reports the interruption and can be rerun. If the
verification task has been cancelled or its recovery attempts are exhausted,
`research resume` shows the verification queue stop reason.

For a restricted scope with a separately configured and approved local model,
include `--lean-model-rounds N --confirm-local` when proposing the policy. Review
the bound local configuration when approving its hash. A changed model
configuration needs its own policy. The worker continues to use the original
statement and approved basis. See the [project model budget](project-model-budget.md)
for the workspace-wide reservation that applies alongside the goal call limit.

## Prepare a follow-up from an adopted proof

Run `research-multiplier research follow-up CLAIM_ID` to see candidate
directions supported by the current proof structure. For example, an unused
premise is a reason to investigate a separate weaker scope, and a proved
conjunction may contain a separately reusable result. The returned options are
proposals.

Provide the exact option digest with `--prepare` to draft a separate research
frame with the retained approved basis. Confirm that frame, then use
`research follow-up FRAME_ID --propose` to bind the candidate proposition to it.
Select and independently verify the new frozen goal through the normal process.
