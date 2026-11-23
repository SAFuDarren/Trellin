# Check a research goal with Lean Core

Use the `lean-core` domain in [a research goal frame](research-goals.md). Each hypothesis and the candidate conclusion needs a closed Lean proposition in `formal.lean.proposition`. The verifier constructs the statement by adding the approved hypotheses as antecedents to the selected conclusion, in their recorded order.

With Lean 4.19.0 installed, draft the exact statement, read it and record your confirmation.

```sh
research-multiplier lean draft-theory PACKET_ID
research-multiplier lean show STATEMENT_ID
research-multiplier lean confirm STATEMENT_ID --version VERSION --hash STATEMENT_HASH \
  --actor Researcher --reason "The formal statement matches the research question"
```

Standalone Lean Core uses a trusted native Lean 4.19.0 installation on macOS or glibc Linux. Its reviewed environment binds the executable, all compiled files imported by Lean Core, and the resolved native libraries. macOS system images supplied by the dynamic loader's shared cache are identified by the cache UUID. The verifier checks the pinned files before and after a successful proof.

Structured checks and standalone Core checks use the reviewed runtime. Use the [native source workflow](#check-a-lean-source-file-or-compiled-declaration) to recheck proofs after a Lake rebuild when the frozen scope is unchanged. Standalone Core evidence recorded before runtime measurement remains in history and needs rechecking before promotion or reuse.

Pass a structured proof term to the verifier. For example, if the hypothesis is `True` and the conclusion is `True ∧ True`, introduce the hypothesis and use it twice.

```json
{"format_version":1,"proof":{"intro":{"count":1,"body":{"and":[{"use":0},{"use":0}]}}}}
```

```sh
research-multiplier lean check-theory STATEMENT_ID proof.json
research-multiplier review packet EVIDENCE_ID
```

The grammar supports introduction with `intro`, references to introduced variables with `use` (zero-based), and `apply`, `and`, `left` and `right`. Use `{"rfl":true}`, `{"decide":true}` or `{"trivial":true}` for fixed proof operations. To cite an approved Lean Core theorem use `{"ref":"VERSION_UUID"}`. Its review item needs `kind: "theorem"`, the approved `formal.lean.proposition` and an exact `formal.lean.declaration` name in the installed Lean Core library.

The verifier accepts a fixed set of Lean Core syntax for closed propositions, including basic logical connectives, quantifiers, arithmetic, and named Core declarations. It checks the syntax before elaboration. It then compiles the generated proof independently, checks its type and kernel dependencies, and records the theorem versions actually used. The evidence proceeds through human review.

### Accepted proposition syntax

Propositions, and the types of shared parameters, may use only this syntax, because other term elaborators can run code before the kernel checks anything:

- Names, numbers, application and parentheses, type ascriptions `(x : T)`, `Prop`, `Type` and `Type u`.
- `∀` and `∃` with explicit `(x : T)` and implicit `{x : T}` binders, and `→`.
- `∧`, `∨`, `¬`, `↔`, `=`, `≠`, `<`, `≤`, `>`, `≥`, `+`, `-` (binary and unary), `*`, `/`, `%` and `^`.

`intake preview-target`, `intake freeze-target` and `intake draft` stop a Lean proposition that uses other syntax before anything is frozen. The message names each construct and a rewrite. These are the common ones from Mathlib-style statements:

| Written | Rewrite |
| --- | --- |
| `[Fintype X]` (instance binder) | `(iX : Fintype X)` |
| `ℕ`, `ℤ`, `ℚ`, `ℝ` | `Nat`, `Int`, `Rat`, `Real` |
| `\|a - b\|` | `abs (a - b)` |
| `∑ C ∈ M, f C` | `Finset.sum M f`, or a named definition |
| `x ∈ s`, `∃ C ∈ M, P` | `Membership.mem s x`, `∃ C, Membership.mem M C ∧ P` |
| `∅`, `⊥`, `⊤` | `EmptyCollection.emptyCollection`, `Bot.bot`, `Top.top` |
| `{α : Type*}` | `{α : Type}` |
| `fun x => …`, `⟨a, b⟩`, `if … then … else …` | a named definition in your Lean project, or the constructor by name |

Put definitions that need richer syntax in your own Lean project and refer to them by name. A statement frozen before this check that the audit later refuses makes `lean draft-theory` name the same constructs and rewrites.

Use `lean check-theory STATEMENT_ID --model` with a configured [Lean author](model-adapters.md) to generate the proof document. The author gets the approved public proposition and its admissible basis. A basis version from a restricted or unclassified source needs separate authorization for model input. Review the exact version and record the decision with `research release-item VERSION_ID --hash VERSION_HASH --actor Researcher --reason REASON`. The candidate passes through the same independent checker.

For a restricted goal, supply the structured proof file directly or use [`lean check-source`](#check-a-lean-source-file-or-compiled-declaration). Both use native Lean with zero model calls. Review and promotion use the same commands as for public goals. The theorem catalog exposes promoted public structured proofs.

## Refute a frozen Lean proposition

Use `--refute` with a structured proof of the negation of the entire frozen proposition. For the goal `H₁ → H₂ → P`, Lean checks `¬(H₁ → H₂ → P)` in the same confirmed environment. Introducing the outer binder gives the proof an assumed implication `H₁ → H₂ → P`. To apply it, the proof must establish each antecedent.

```sh
research-multiplier lean check-theory STATEMENT_ID refutation.json --refute
research-multiplier review packet EVIDENCE_ID
```

A successful check stores a `counterexample` receipt with `refutation_mode` set to `logical_negation`. The report shows the original scope, the actual checked negated type, its dependencies, and whether the implication binder occurs in the proof term when Lean exposes it. This path records an exact logical refutation and leaves the explicit witness field empty. The claim remains open for human review and can be marked refuted by an approved promotion.

## Reuse a checked theorem in another claim

### Replay a native Lean theorem

When you adopt a checked `.lean` or Lake proof, `lean theorems` shows its exact reusable version. In the same research workspace, pass that version and the destination's confirmed Lean statement ID to `lean reuse-theorem`.

```sh
research-multiplier lean theorems
research-multiplier lean reuse-theorem DESTINATION_STATEMENT_ID THEOREM_VERSION_ID
```

For a destination that uses a compiled Lake project, also pass `--mathlib-project PATH`. The destination supplies its reviewed definitions and theorem declarations. The program reads the archived kernel proof, checks the original conclusion and the full ordered list of premises, and compares the relevant declarations in the destination environment. The proof may be replayed after the original source file or candidate Lake module has been removed.

The checker tries to discharge the original premises from the destination's confirmed hypotheses and admitted theorem versions. It uses bounded, deterministic rules for direct matches and logical construction or projection. A blocked replay identifies the premises or declarations that still need attention. A successful replay creates a destination evidence receipt without asking for another scope confirmation or invoking a model. Review and adopt the result through the ordinary evidence workflow.

An adopted native theorem is also available through `ref` inside a structured
proof under the destination's original hypothesis binders. For example, when
the destination has one premise and its conclusion is the native theorem's
conclusion conjoined with `True`, the proof can use

```json
{"intro":{"count":1,"body":{"and":[{"ref":"NATIVE_THEOREM_VERSION_UUID"},{"trivial":true}]}}}
```

Lean supplies the native theorem's prerequisites from the confirmed scope.
In a blueprint use `node_ref` to refer to an adopted predecessor in the
same way. `lean search STATEMENT_ID --rounds 0` tries applicable archived
theorems and checked reductions with zero model calls. Explicitly public
search can also use native references in proposed intermediate lemmas.
An unresolved derivation remains an obligation for the proof route.

Local restricted results can be reused in another restricted claim in the same research workspace. Keep both claims and the content-addressed proof archive in that workspace. When a used source receipt or theorem version is withdrawn, the dependent results enter `needs_recheck`.

For a native receipt checked before proof packages were introduced, run `lean export-theorem EVIDENCE_ID` on the already adopted proof. Supply the original `--mathlib-project PATH` when relevant or `--run-directory PATH` when the checked proof is only available in an earlier run archive. The exporter can rebuild a package when it can reconstruct the original checked inputs and environment.

### Reuse a checked working proof

The signed Lean advancement worker can recheck a working lemma from another
claim in the same workspace while filling an open reduction obligation.
The worker matches equivalent explicit bound names and gets the original
premise arguments from the destination's reviewed scope. It loads the exact
archived source proof and any scoped proofs it cites. Lean checks the
prerequisites in dependency order with their full function types in the
destination. The top proof cites the destination versions and discharges its
source premises using bounded proof search over the destination's approved
theorem versions and checked local lemmas. Every replayed node retains its
exact source and actual destination dependencies. Withdrawing a dependency
marks the affected replayed nodes and conclusions for recheck.

The worker can replay eligible graphs before it calls a model. It uses the
local proof checker's limits on graph size and depth. Its reviewed shared
parameter header, if present, must match the destination's header. A
referenced theorem outside the local proof graph must be available at its
exact approved version. If a premise or reference remains unavailable, the
obligation stays open under the original destination scope.

### Replay a structured Lean Core proof

An exact public proof from the structured Lean Core workflow enters the theorem library after the researcher approves its evidence and applies the receipt. A reuse entry records the exact checked conclusion and the **full list of original antecedents**, with the original statement, checker, environment and proof-document hashes. Use its version ID from `research-multiplier lean theorems`.

In the destination proof, supply the antecedents as arguments. For example, a source theorem with two prerequisites is used as

```json
{"apply":[{"ref":"PROMOTED_THEOREM_VERSION_UUID"},{"use":0},{"use":1}]}
```

If the destination already established these prerequisites by some other derivation, pass those proof terms instead of `use` terms. The kernel checks the arguments against the exact source theorem. A source theorem can also be cited without arguments if the proposition being proved is itself that complete implication.

For a structured file, the verifier registers the cited version against the destination packet. To give a model access to a specific theorem, use `lean check-theory STATEMENT_ID --model --use-theorem VERSION_ID`. The model receives the approved proposition and antecedents. The verifier independently rebuilds the source proof from its checked structured document and pinned theorem references in the destination audit.

The report lists the directly cited and expanded dependency versions separately. For each frozen antecedent it records whether the corresponding binder occurs in the checked proof term. An unexposed binder is recorded as unknown. A claim with fewer assumptions needs its own proof. The report records model author attempts by ID even when a hand-written and model-authored document share the same deterministic receipt. Withdrawing a version or a source receipt removes the theorem from the current catalog and flags dependent evidence for recheck.

New checks keep the reusable proof document in the selected workspace's object store. For a proof created before the theorem library was available, run `lean export-theorem EVIDENCE_ID --run-directory PATH` to recover the hash-checked document from its original run. This requires a promoted proof from the same research database. The replay format uses the constrained Lean Core grammar and has bounded depth, replay size, and execution time.

## Derive and reuse local lemmas

After confirming the original statement, use `lean derive` to check a structured plan in that statement's scope. The verifier supplies the reviewed hypotheses and imports. Each intermediate node specifies a closed consequent and a proof term. A successful node is recorded automatically, and its exact UUID replaces later `node_ref` references. Use the optional `goal_proof` to check the frozen original proposition with those versions.

For example, take the frozen premise `True` and the original goal `True ∧ (True ∧ (True ∧ True))`.

```json
{"format_version":1,"nodes":[
  {"name":"L1","proposition":"True ∧ True",
   "proof":{"intro":{"count":1,"body":{"and":[{"use":0},{"use":0}]}}}},
  {"name":"L2","proposition":"True ∧ (True ∧ True)",
   "proof":{"intro":{"count":1,"body":{"and":[{"use":0},{"apply":[{"node_ref":"L1"},{"use":0}]}]}}}}
],"goal_proof":{"intro":{"count":1,"body":{"and":[{"use":0},{"apply":[{"node_ref":"L2"},{"use":0}]}]}}}}
```

```sh
research-multiplier lean derive STATEMENT_ID lemmas.json
research-multiplier lean local-theorems STATEMENT_ID
research-multiplier review packet EVIDENCE_ID
```

Order each node before its dependents. The result includes the final verification under `goal` and the individual verified versions under `lemmas`. The final evidence uses the ordinary human review and promotion steps. Omitting `goal_proof` checks and records the intermediate plan on its own. A separate structured `lean check-theory` proof or exact refutation can cite a local lemma UUID directly through `ref` on the same statement. Each replayed lemma still has all its original premises. Use `apply` to pass proofs of those premises, including when verifying a refutation.

Lean replays archived proof terms and checks exact types, arguments and transitive dependencies. Final reports show direct and expanded local lemma versions separately from ordinary basis versions. Repeating identical inputs recovers the recorded result. Withdrawing an input marks both the lemma chain and dependent final evidence `needs_recheck`. Earlier verified nodes remain available if a later node fails.

For a restricted statement, use locally supplied files with the scheduler and verifier credentials. For imports from your pinned, compiled Mathlib or Lake project, pass `--mathlib-project` on the verification command. A single `lean derive` plan discovers its approved Mathlib import graph once, including when it checks the final goal. The verifier checks the pinned files at each step. Local lemma IDs and the final proofs that refer to them stay within the original statement's scope. The cross-claim catalog accepts separately replayable proofs.

## Check sufficient conditions for the frozen goal

For a confirmed goal `G` under the confirmed premises `H`, a reduction records a Lean proof of `H → (L₁ → … → Lₙ → G)`. The obligation propositions are required intermediate results. Once each is independently proved under `H`, the program applies the checked reduction and sends the original goal through Lean again.

A version 1 reduction document contains `format_version`, an ordered `obligations` list of one to eight distinct Lean propositions, and a structured `proof` of the implication. Use the existing proof term format and current allowable theorem references. Lean checks the implication against the frozen goal assembled by the program.

```sh
research-multiplier lean reductions STATEMENT_ID --register reduction.json
research-multiplier lean reductions STATEMENT_ID
research-multiplier lean derive STATEMENT_ID local-lemmas.json
research-multiplier lean reductions STATEMENT_ID --work
```

For a Mathlib-backed statement, pass `--mathlib-project PATH` to the checking commands. Local obligations are matched by their exact recorded proposition text. The checking commands work across process restarts. An obligation proved within the frozen scope can be used without an intermediate human approval. The final receipt enters the normal review queue. Withdrawing the implication, an obligation proof, or a used verifier input takes affected receipts out of current use.

## Track a proof blueprint

Give each subgoal its own frozen goal and Lean statement ID. A blueprint connects nodes by name, without sharing Lean declaration names between their proofs. A node's proof is optional while its approach is being worked out.

```json
{"format_version":1,"nodes":[
  {"name":"lemma","statement_id":"LEMMA_STATEMENT_UUID","proof":{"trivial":true}},
  {"name":"target","statement_id":"TARGET_STATEMENT_UUID","proof":{"node_ref":"lemma"}}
]}
```

Run `lean blueprint blueprint.json` to see node status in dependency order. Use `--work` to check ready nodes with the same independent verifier. When a referenced node's proof is approved and its receipt applied, the blueprint substitutes its exact exported version ID for `node_ref`. As with a direct theorem reference, supply any antecedents using `apply`. Each node still has an independent statement review and result review.

A node's proof can cite a locally verified lemma with `{"ref":"LOCAL_LEMMA_UUID"}` on that node's exact Lean statement. An applied result without a cross-claim export shows `proved_without_export`. A node still waiting to cite that result through `node_ref` shows `blocked_by_unexported_theorem`. Use an exportable proof for any node you intend to cite from another statement.

## Check a goal with your native Lean project

Use the root of your existing Lean Lake project with Lean 4.19.0 (commit `6caaee842e94`), a pinned Lake manifest, and compiled imports. The checker uses the project's Mathlib modules, dependency modules and any compiled declarations from the project itself. Build any missing imports from the reviewed Lake project before drafting. If you need a fresh Mathlib environment, run `research-multiplier --workspace /path/to/research lean prepare` and run Lake in that directory to build or fetch the imports.

From your Research Multiplier workspace, provide the Lean project path and the imports needed for the frozen proposition and its approved basis.

```sh
research-multiplier lean draft-theory PACKET_ID \
  --mathlib-project "/path/to/your/Lean-project" --import Mathlib.Data.Real.Basic
research-multiplier lean show STATEMENT_ID
research-multiplier lean confirm STATEMENT_ID --version VERSION --hash STATEMENT_HASH \
  --actor Researcher --reason "Reviewed the elaborated type, premises and Mathlib imports"
research-multiplier lean check-theory STATEMENT_ID proof.json \
  --mathlib-project "/path/to/your/Lean-project"
research-multiplier review packet EVIDENCE_ID
```

Repeat `--import MODULE` in the order you want Lean to load modules. Set `RM_LEAN_MATHLIB_PROJECT` to the project path to use it as the default for drafting, checking and running a blueprint. If you keep multiple Lean versions installed, pass `--lean /absolute/path/to/lean` to select the pinned compiler.

The draft records the elaborated type and the checking environment. Review these alongside the frozen statement and assumptions. The verifier reads compiled modules from the selected project and writes generated check files in a temporary directory. Record any named theorem intended for use as an approved basis item with its exact Lean declaration and proposition. The verifier reports actual declaration and module dependencies.

Replaying an exported structured theorem requires its approved environment and ordered imports. For revised reviewed imports or a changed research scope, generate and confirm a new statement version. Prior evidence stays in the workspace history.

To run the native Mathlib regression, use the [external Lean regression suite](../CONTRIBUTING.md#run-the-external-regression-suite) with the prepared Lean project and a disposable database.

## Check a Lean source file or compiled declaration

Select and bind the research workspace, then [draft and confirm](#check-a-research-goal-with-lean-core) the exact statement and its Lean environment. Run `check-source` with the statement ID and the theorem's declaration name. The command uses native Lean and makes zero model calls for public or restricted goals.

For a local file, supply a UTF-8 `.lean` file with reviewed imports and one or more plain theorem declarations. A theorem can have a Lean term or a `by` tactic block as its proof. Use the exact declaration name, such as `MyNamespace.theoremName`, to select one theorem, including any Unicode characters. The file can use namespaces, sections, variables, `open`, and `open scoped`. The audit follows referenced local helpers. Lean checks the syntax before compiling the same file contents. Files with arbitrary commands or authored definitions belong in your Lake project. The preflight rejects constructs such as `run_tac`, `by_elab`, `native_decide`, and `sorry`.

```sh
research-multiplier lean check-source STATEMENT_ID inputs/proof.lean --declaration THEOREM_NAME
```

Pass `--mathlib-project PATH` for a Mathlib-backed statement. For a theorem already compiled in a pinned Lake project, include its defining module in `lean draft-theory … --import MODULE`, review the resulting statement, then give the module and the exact qualified theorem name. Build the module in that Lake project first.

```sh
research-multiplier lean check-source STATEMENT_ID --declaration QUALIFIED_NAME \
  --module MODULE --mathlib-project "/path/to/your/Lean-project"
```

The verifier checks the selected declaration's type against the complete frozen proposition, including all confirmed premises. It independently checks the proof in the kernel and audits actual transitive dependencies and axioms. Register named mathematical facts used directly by the proof, including facts cited by tactics. Record each fact's exact `formal.lean.declaration` and `formal.lean.proposition` in an approved theorem version and attach it to the packet. A direct citation such as `Nat.le_refl` follows this rule even though it lives in Lean's standard library. Kernel constructors and recursors can construct proofs directly. Declaration names in basis entries use ordinary qualified ASCII Lean names. The report shows actual theorem versions, their dependencies, and whether each frozen premise binder occurs in the proof term.

After rebuilding a Lake module, repeat the same `check-source` command with the existing statement ID. The program compares the Lean meaning of the frozen proposition, its premises, and the approved declarations with the recorded scope. Proof edits and rebuilds that preserve the scope are checked directly. The receipt records the new runtime digest alongside the original statement and measured scope. If the meaning changes, the command reports `lake_scope_review_required` with the affected scope or declaration. A candidate that proves a different proposition is rejected under the existing frozen statement.

For a statement reviewed before scope measurements were introduced, the program can measure the original build and can recover it from archived modules when the entire reviewed runtime matches. If it cannot recover that build, supply the original reviewed project or draft and review the scope once with the current build.

The command archives the checked source or compiled module and saves successful reports by attempt under `runs/lean/` in the workspace. A successful check returns an evidence ID for the ordinary review and promotion workflow. Adopt the receipt to make its checked native theorem [available for cross-claim replay](#replay-a-native-lean-theorem).

```sh
research-multiplier review packet EVIDENCE_ID
research-multiplier review-evidence EVIDENCE_ID approve --actor Researcher --reason "Reviewed the exact proof and scope"
research-multiplier promote-evidence EVIDENCE_ID --reason "Apply the reviewed receipt"
```
