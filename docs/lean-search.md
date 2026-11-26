# Search within a reviewed Lean statement

Search starts with the frozen proposition, its confirmed premises and the pinned Lean environment. Run without a model to find proofs from the available basis and previously checked lemmas.

```sh
research-multiplier lean search STATEMENT_ID --rounds 0
```

The program replays available native proofs, checks ready reductions, and tries a bounded set of structured proofs. The structural search uses exact facts, implications, conjunctions, disjunctions and reflexive equalities. An unresolved conjunction can yield a checked reduction to its conjuncts. The program checks intermediate obligations as independently scoped lemmas, then rechecks the complete frozen goal as soon as a route is ready. A failed attempt leaves an open proof obligation.

For a public scope you can also authorize proposals from a configured [Lean author adapter](model-adapters.md). Use `--confirm-public` before sharing the exact goal and its approved basis with the model.

```sh
research-multiplier lean search STATEMENT_ID --confirm-public --rounds 3
```

A proposal can establish a local lemma, supply an implication for a new route, prove the goal, give a proof of the negation of the entire frozen statement, or report an obstacle. A new route must prove that its listed obligations suffice for the original goal under the frozen premises. The obligations remain open until independently checked from those premises. Distinct routes may make progress independently and reuse the same scoped lemmas. All candidate proofs pass the normal Lean verifier.

The search returns the proposal and verification attempt IDs and a content-addressed report. Existing proof terms and the proof receipts they use remain inspectable through `lean local-theorems`, `lean reductions`, and `review packet`. Disclosure also includes archived model proposals behind checked local lemmas used transitively. Exact proofs and exact refutations enter the usual evidence review. Open obligations stay in the search or route record. A full review quota pauses further work with `awaiting_review_capacity`.

A route can carry its proofs in `obligation_proofs`, a list parallel to
`obligations` with structured proofs or `null` entries. The program tries
available deterministic derivations first. If a submitted proof is needed,
Lean checks it independently under the original scope and records the actual
basis and local lemmas. A submitted proof can refer to an earlier checked
obligation as `{"node_ref":"Obligation1"}` using its one-based position.
The reference is a scoped theorem and takes the reviewed parameters and
hypotheses as arguments. Once all obligations verify, the same worker pass
can check the original theorem without another model call.

The scoped proof language can eliminate an already established disjunction
with `or_elim` and two case handlers, project a verified conjunction with
`and_left` or `and_right`, and reverse a verified equality with `eq_symm`.
Lean checks each branch and keeps its local assumptions inside that branch.

Use up to eight proposal rounds per invocation. A search checks at most three new model-proposed reductions. Repeating a recorded route can recover its calls and verified results. When a checked lemma completes a reduction during search, the program checks the original goal in the same invocation, including on the last permitted model call. A rerun reuses the current pending or approved reduction receipt while the claim is open.

Model-assisted searches for a frozen goal share a [persistent budget](lean-search-budget.md). View usage and reservation history with `lean budget STATEMENT_ID --history`. The [adapter guide](model-adapters.md) describes the explicit local route for restricted research.

## Continue a signed search across worker passes

A signed Lean advancement policy gives the worker a bounded number of model
proposals. Each pass first checks what the program can derive from the
current confirmed basis. It can make one model proposal, then save exact
references to the failed route, checked reduction, or verified local lemma.
The next pass reads that checkpoint. A blocked method can lead to an
alternative reduction or a refutation attempt. When a checked route has an
open obligation, the model can work directly on that proposition or a
prerequisite. See [signed advancement](research-progress.md) for the commands.

A worker also tries bounded counterexample search on supported closed and
finite-variable Nat and Bool expressions. Any selected assignment must satisfy
the original premises and guards. A candidate becomes reviewable only after
Lean verifies the negation of the complete frozen statement. An exhausted
bounded search leaves the question open.

The worker can split a nested conjunction beneath explicitly typed universal
binders and implications. Every generated obligation keeps those binders and
implications in order. Lean checks the reduction, each intermediate lemma and
the final proof against the frozen scope. Other goal shapes remain available
to the existing checked search and model proposal interface.

Worker passes report checked, untried and outstanding obligations. A
failure follows the exact unresolved proposition within the frozen scope, so
a different obligation on the same route can still advance. If every obligation
has already stalled, search tries an alternative reduction or exact
refutation within the signed budget. Invalid model responses count toward the
failed obligation and model allowance. A checked lemma that leaves all open
route obligations unchanged stays available for reuse. That round counts as
stalled for route selection.

Before reserving a model call, the worker checks available program proofs
for the selected routes. It can retrieve a current working lemma from another
claim in the same workspace when a pending obligation matches after
normalizing leading explicit binder names. Lean checks the replay in the
destination under the destination's frozen premises, with bounded use of
its approved theorem versions and checked local lemmas. The replay records
both the exact source and each destination fact used to discharge a premise. If the source is later
withdrawn, downstream evidence is marked for recheck.

For a disjunction under supported binders, the worker can check each branch as an
independent sufficient route. An exact local proof that one route obligation fails
under the full frozen scope retires that route from search. Its witness and proof
reference appear under `route_attempts`; the original goal stays open while the
worker tries a different checked route. The model can attach a concise `method`
to a reduction proposal. Candidate identity ignores that label and normalizes
explicit bound names to avoid repeating the same obligation under a rename.

The worker records a failed ready route against its exact implication and
obligation versions before testing another ready route. It rechecks the current
scope and runtime before proceeding; a context change or receipt commit error
stops work. The archived report includes checked route transitions and model
usage where reported. `research resume` surfaces the reason for a blocked route
as part of the current research status. The workbench shows the same checked
lemmas and failed routes in the claim's [verified ledger](proof-workbench.md#read-the-verified-ledger);
the next round reads route failures through the same function.

For supported formulas over a bounded Boolean scope, the worker evaluates every
assignment after checking available reductions. It submits a structured decision
proof only if no assignment satisfying the original premises falsifies the goal.
Lean elaborates that proof against the exact frozen proposition and independently
audits the resulting closed theorem. The program records the number of assignments
and uses no model call for this decision path.

### Run several independently checked routes

Sign the total model allowance, the number of simultaneous proof routes, and
how many consecutive stalled checkpoints can pause a route. One route remains
the default. Parallel policies take two or three routes, at most eight model
calls in total, and one to three stalled checkpoints before a pause.

```sh
research-multiplier advance propose --for-packet PACKET_ID --lean-model-rounds 6 \
  --lean-route-parallelism 2 --lean-route-stall-limit 2
research-multiplier advance approve POLICY_ID --hash POLICY_SHA256 --actor RESEARCHER --reason 'Review two proof routes'
research-multiplier advance worker --policy POLICY_ID --parallel 2
```

The worker finishes available deterministic checks first. One pass can assign
one unanswered proof obligation per verified route, up to the signed width and
the worker's `--parallel` slots. The requests are recorded together before
calling the model. Each request receives its route and its own failed-attempt
record, plus the common frozen statement and verified facts. Responses run on
separate role connections and are admitted in the recorded order by the normal
Lean verifier. An already recorded result is reused after an interruption.

The database counts every reserved call, including recovery leases, toward the
signed policy allowance and toward the frozen goal's allowance. After the
signed number of stalled checkpoints, the worker gives the route's future
turns to a route that can still progress. A newly verified shared fact can
reactivate an affected route. When all remaining routes are paused, `advance
status` and `research resume` retain the open obligations. Once a route closes,
additional responses from that pass remain archived as unadmitted proposals.
