# From research notes to an approved starting basis

Choose the research workspace before intake. The original input and optional model transcript are archived by content hash in that workspace. Drafts and decisions are recorded in its configured PostgreSQL database.

## Freeze a known target before proposing assumptions

Record your question and its proposed formalization in `inputs/target.json`.
For example, use this local Lean target:

```json
{
  "title": "Reflexivity on the natural numbers",
  "domain": "lean-core",
  "sensitivity": "restricted",
  "statement": "Every natural number equals itself.",
  "formal": {"lean": {"proposition": "∀ n : Nat, n = n"}}
}
```

A model can prepare the formal proposition. Use
`intake propose-target inputs/question.json --confirm-public` for public material.
For restricted material use `--confirm-local` and a
[trusted local model](model-adapters.md#work-with-restricted-research-in-the-terminal).
Provide the same metadata and `statement` fields, plus optional textual
`context`. When the model returns a target, use
`intake preview-target --from-proposal PROPOSAL_SHA256` to review the exact
archived proposition without copying it. Use that same `--from-proposal`
value with `intake freeze-target` and sign the hash from the preview.
Resolve any returned interpretation questions before freezing.

```sh
research-multiplier intake preview-target inputs/target.json
research-multiplier intake freeze-target inputs/target.json --hash TARGET_HASH \
  --actor Researcher --reason "Checked the meaning of the conclusion"
research-multiplier intake draft inputs/notes.json --target TARGET_ID
research-multiplier intake show INTAKE_ID
```

At this step the target is a pending research question and carries no proof
or premise authority. The target-specific document can contain an
`items` array, public `notes` for `--model`, or restricted `notes` for
`--confirm-local`. Read the
listed definitions, hypotheses and theorem versions, then confirm the
exact intake and decisions as described below. A single database transaction
will bind the approved basis to the original target and freeze the proof
packet. `intake target TARGET_ID` reports the resulting `packet_id`.

The [local Lean checker](lean-core.md) prepares the executable statement
from that packet. Review the exact elaborated statement and pinned
environment when activating that checker for the first time.

## Use shared Lean parameters

For a target and hypotheses about the same objects, declare their ordered
parameter names and Lean types in each reviewed Lean formalization. For
example, the goal that two maps compose injectively can use this formal part.

```json
{
  "lean": {
    "parameters": [{"name": "f", "type": "Nat → Nat"}, {"name": "g", "type": "Nat → Nat"}],
    "proposition": "∀ a b : Nat, g (f a) = g (f b) → a = b"
  }
}
```

Supply each hypothesis as its own intake item. Give it the same
`formal.lean.parameters` array. The first map's hypothesis can have proposition
`∀ a b : Nat, f a = f b → a = b`, while the second map's hypothesis uses
`∀ a b : Nat, g a = g b → a = b`. The reviewed proof has one outer
`∀ (f g : Nat → Nat)` and an implication for each approved hypothesis.
Review the exact elaborated statement with `lean draft-theory` and `lean confirm`.

Keep parameter declarations in dependency order. The name, type and position are
part of each hypothesis version. The same order applies to a structured proof's
initial binders, followed by the frozen hypotheses in statement order. A derived
lemma or a reduction obligation may mention the same parameters. It acquires no
additional premises. Use `research basis PACKET_ID` to see the active versions
and `research retire-item VERSION_ID --reason REASON` to withdraw one
hypothesis version and mark affected proofs for recheck. To use a changed
parameter type or hypothesis, freeze and review the corresponding new scope.

## Prepare the notes

Save the relevant definitions, hypotheses and already established theorems in the structured notes. The objective describes the research direction. A conjecture itself can be proposed for the confirmed goal later.

```json
{
  "title": "A small example about natural numbers",
  "domain": "lean-core",
  "objective": "Check whether the property holds under the agreed assumptions.",
  "sensitivity": "restricted",
  "items": [
    {
      "kind": "hypothesis",
      "natural": "The example uses True.",
      "formal": { "lean": { "proposition": "True" } }
    },
    {
      "kind": "hypothesis",
      "natural": "Zero is reflexively equal to itself.",
      "question": "Is the intended type Nat?"
    }
  ]
}
```

```sh
research-multiplier intake draft inputs/notes.json
research-multiplier intake show INTAKE_ID
research-multiplier review inbox
```

The program groups exactly duplicated entries. It compares both the exact wording and the structured statement with existing approved, current versions. An exact match is reusable by version ID. Unresolved wording or different structured content remains a distinct item for review. An explicit question always requires a decision.

For free-text public notes, set `sensitivity` to `public`, put the text in `notes`, and add `--model` to draft. The configured `EXPLORE` adapter proposes candidates with wording present verbatim in those notes. A person confirms scope for each such candidate, even when the library already contains it. For a frozen public target, model extraction can flag a possible premise required by the original wording. Each question carries a verified quotation from the target or notes. The proposed formal statement becomes a hypothesis only after an explicit item decision. An exactly matching premise already stated in this intake does not produce a second implicit-premise question. Structured notes take the deterministic route by default. An explicitly empty `items` array declares an empty starting basis.

## Confirm outstanding items

Read the draft and keep its exact `intake_sha256`. Save only the outstanding decisions in a JSON object keyed by the displayed item IDs. An accepted ambiguous item needs the clarified formal statement. An omitted item needs a specific reason.

```json
{
  "i2": {
    "action": "accept",
    "formal": { "lean": { "proposition": "(0 : Nat) = 0" } },
    "reason": "The intended type is Nat."
  }
}
```

```sh
research-multiplier intake confirm INTAKE_ID --hash EXAMINED_SHA256 \
  --decisions inputs/decisions.json --actor Researcher --reason "Reviewed the original scope and basis"
```

When every item already matches, the confirmation needs no item decisions. If you change a formal statement that was already present in the draft, refresh the draft so the changed meaning is visible. The database approves new accepted versions and starts the goal in one transaction. Reused versions keep their original approval. With a frozen target the result is the bound proof packet. When starting
without a target, the result is a confirmed frame for the
[goal and conjecture workflow](research-goals.md).

## Answer an unresolved interpretation

Use `research resume --format text` to find material questions raised while
drafting a target or formalizing a discussed conjecture. Each request is linked
to the exact saved input and, for a conjecture, to its confirmed frame.
Opening or answering a question needs no model call. Store the answers as a
JSON array in the order shown by the command.

```sh
research-multiplier research clarify REQUEST_ID
research-multiplier research clarify REQUEST_ID --answers inputs/answers.json \
  --actor Researcher --reason "Confirmed the intended carrier"
research-multiplier research clarify REQUEST_ID --continue --confirm-public
```

Use `--confirm-local` for restricted work. Continuing sends the exact draft and
recorded interpretations to the configured formalizer. The returned target
or conjecture still goes through its usual review and confirmation. A
repeated question is kept out of the review queue. If clarification is no
longer needed, record `--dismiss` with the researcher and reason.
