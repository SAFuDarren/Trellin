# Review a research basis and choose a conjecture

A goal frame records the research question and the exact versions of its starting assumptions, definitions and theorems. Choose and freeze a conjecture when its natural wording and formal statement reflect your intent. When the target is already known, use [target-first intake](research-intake.md#freeze-a-known-target-before-proposing-assumptions)
to review the target and starting basis in that order. For an open research direction, start with a frame and compare proposed targets under the same basis.

## Record the basis

Use the [versioned basis commands](research-foundation.md#confirm-the-basis-and-run-the-program) to propose and approve the exact versions of the items you want to use. The version ID, rather than an item's latest content, goes into the frame. Set the frame's sensitivity explicitly. For example, save this as `goal.json`, using your approved version IDs in place of the placeholder.

```json
{
  "title": "A simple consequence of two assumptions",
  "domain": "lean-core",
  "sensitivity": "public",
  "objective": "Identify what follows from the approved starting assumptions.",
  "basis_version_ids": ["APPROVED_VERSION_UUID"],
  "progress": {"scope": "An exact proof from the approved basis"}
}
```

```sh
research-multiplier goal draft goal.json
research-multiplier goal show GOAL_ID
research-multiplier goal confirm GOAL_ID --hash FRAME_SHA --actor Researcher --reason "Checked the meaning of the starting basis"
```

The frame keeps progress notes for review. Its proof status and counting of verified results are separate.

## Compare and select candidates

Write each candidate in its own JSON file. Enter its exact statement, a structured formalization and provenance. For a Lean Core candidate, use a closed Lean proposition for the conclusion or declare its [shared typed parameters](research-intake.md#use-shared-lean-parameters). Each hypothesis has its own `formal.lean.proposition`. In a parameterized scope every hypothesis binds the same exact ordered parameter context. The checker builds one quantified proposition with those frozen hypotheses as antecedents and the selected conclusion as consequent.

```json
{
  "statement": "True and True both hold.",
  "formal": {"lean": {"proposition": "True ∧ True"}},
  "provenance": {"kind": "human"}
}
```

```sh
research-multiplier goal propose GOAL_ID conjecture.json
research-multiplier goal show GOAL_ID
research-multiplier goal select CONJECTURE_ID --hash CONJECTURE_SHA --actor Researcher --reason "Confirmed the exact target"
```

You can record more than one candidate before selecting. An imported model proposal uses `provenance.kind` equal to `ai` and keeps any available model ID, transcript reference and origin details. A proposal carries the frame's approved basis without modifying it. Selection freezes one exact conjecture and returns its claim and formalization packet IDs.

Run `research-multiplier goal suggest GOAL_ID` to request alternatives from the configured [exploration model](model-adapters.md). This is available for confirmed public frames. The request includes the public objective, domain and exact approved versions with their natural and formal statements. Suggestions are written with their model identity, prompt hash and archived transcript. View the alternatives with `goal show` and select the one that matches your research intent.

For a discussion before formalizing any candidate, use
`goal discuss GOAL_ID inputs/intuition.json --confirm-public` with an
`{"intuition":"..."}` document and a confirmed public frame. The command shows
natural-language candidates and the approved basis versions behind them.
With a restricted frame, use `--confirm-local` for discussion and formalization.
Run `research resume --format text` to see whether a frame has a discussion or
formalized proposals waiting. `goal discussion GOAL_ID` reopens the current
archived discussion and prints the formalization command for each candidate.
To formalize one of the archived candidates without copying its wording,
pass the returned `record.sha256` and the candidate's position (starting at
1). The formalization remains an unselected proposal and retains the source
discussion, position and model transcript. If the model returns a material
question, answer it from [`research resume`](research-intake.md#answer-an-unresolved-interpretation).
Read the proposed wording and Lean
proposition before selecting its exact digest. The original confirmed basis
stays attached to the selection.

```sh
research-multiplier goal formalize GOAL_ID DISCUSSION_SHA 1 --confirm-public
research-multiplier goal show GOAL_ID
research-multiplier goal select CONJECTURE_ID --hash CONJECTURE_SHA \
  --actor Researcher --reason "Checked the intended statement and exact formalization"
```

## Reopen a prepared conjecture in Workbench

Open **Review** and choose a **Conjectures to compare** for a confirmed Lean goal. The panel shows the goal, approved basis with its exact versions, formal statements and recorded sources, and any saved discussion. Prepared candidates show their exact formal statements. A saved discussion can be read locally.

Choose a prepared candidate and record your reason to confirm it. Workbench opens the resulting claim with the frame's complete approved basis. A withdrawn basis or open interpretation question prevents selection. Use [the ordinary proof task flow](proof-workbench.md#start-a-proof-task) to confirm the Lean statement and approve proof work for the resulting claim.

## Handle a questioned condition

Use `goal condition GOAL_ID FILE` when a possible condition materially changes interpretation or a proof route. The JSON file includes `kind` (`interpretation` or `route_condition`), `statement`, `formal`, `question`, and `impact`. Optionally include `replaces_version_id` if it concerns a specific hypothesis already in the frame.

Use `goal decide-condition REQUEST_ID ACTION --actor Researcher --reason "Decision"` to record the outcome.

- Use `reaffirm --version VERSION_ID` when the existing approved hypothesis has the same formal meaning.
- Use `reject` when the question can be closed under the existing scope. A condition requested only for a proof route cannot revise the research frame.
- Use `revise --version VERSION_ID` when the intended scope genuinely changes. The matching new hypothesis must already have an individually approved version. The command creates a separate, unconfirmed frame. Confirm it and select its conjecture before proving it. Once selected, the new claim records which frozen claim it supersedes.
- Use `derived --evidence EVIDENCE_ID` when an independent exact verifier has proved the same formal statement under the approved scope. The database checks the actual proof dependencies, including the imports behind reused theorems, against the frame's approved versions and any definitions or theorems explicitly admitted to its frozen packet. The request retains the evidence ID and shows whether the proof still discharges the condition.

An unresolved interpretation question pauses selection and holds the proof workflow for an already selected claim. A route question remains local to that line of investigation. Formal statements, approval reasons and earlier decisions stay visible in the goal history. If an interpretation depended on a returned or withdrawn proof, open another review with `goal reopen-condition REQUEST_ID --actor Researcher --reason REASON`.

The verifier can run `goal record-derivation REQUEST_ID EVIDENCE_ID` when independent exact evidence has discharged a route condition. The database checks the same statement, exact evidence, starting basis and
actual transitive dependencies before recording the disposition. Previously
recorded dispositions that lack this support stay in history and return to
review. Approving an interpretation of the research question remains a
human decision. For a target-first goal, automated attempts can record a
route obstacle. The review inbox surfaces an exact verified refutation for
the researcher to inspect against the original assumptions.

An independently checked local lemma can close an exact route condition without
a separate claim. Run `goal record-local-derivation REQUEST_ID LEMMA_ID` with the
verifier role. The statement must match the requested Lean proposition and
belong to the same confirmed claim. When the lemma becomes stale, the route
condition reopens. The original interpretation review remains separate.

When an upgrade reopens a previously recorded derivation, `goal show` retains
its original evidence and shows the replacement request ID. Use the replacement
request for the next decision. An interpretation question holds proof of the
same frozen goal until the researcher resolves it.
