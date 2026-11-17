# Review evidence and original literature

Use `research resume --format text` for pending research decisions and the
current next step. `review explain EVIDENCE_ID` reads the frozen claim and
the existing checked record. It shows Lean's checked proposition, reported
premise usage and referenced results. The full provenance remains available
through `review packet EVIDENCE_ID`.

The review inbox combines pending research frames, conjecture selection, statement confirmations, material conditions and clarifications, exact evidence, and archived-source confirmations. Narrow the inbox to one claim with its recorded ID.

```sh
research-multiplier review inbox
research-multiplier review inbox --claim CLAIM_ID
research-multiplier review packet EVIDENCE_ID
```

An evidence packet brings together the frozen statement, actual verifier result, scope, checked proof steps, model and auditor provenance, and questions still waiting for a person. Use `--run-directory` to locate archived artifacts from an older run. The recorded artifact reference remains visible when the local copy is unavailable. Store your explanation and takeaway alongside the proof before recording your decision.

```sh
research-multiplier review understanding EVIDENCE_ID --actor "your name" \
  --explanation "Describe the argument in your own words" --lesson "State what the result taught you"
research-multiplier review-evidence EVIDENCE_ID approve --actor "your name" --reason "Meaning and evidence checked"
```

## Archive a specific original source

Associate a reference with a claim. Create a metadata file with `title`, `locator` (such as a theorem number and page), `excerpt` from the original and a `convention_map` JSON object. Record any notation or conditions that change between the source and project. If the conventions already match, record that in your review reason.

```sh
research-multiplier review import-source CLAIM_ID doi:10.1234/example inputs/original.pdf --metadata inputs/source.json
research-multiplier review export-source SOURCE_ID inputs/original-to-review.pdf
research-multiplier review confirm-source SOURCE_ID --hash SOURCE_SHA256 \
  --actor "your name" --reason "Matched the archived original, theorem and conventions"
research-multiplier review sources CLAIM_ID
```

The import archives the original by content hash in the workspace's objects directory. The source confirmation separately binds the PDF, reference and theorem metadata. A supplied local PDF and identifier remain an operator assertion until then. For a versioned arXiv original, explicitly request a download with `review fetch-source CLAIM_ID arXiv:2401.01234v1 --metadata source.json`. Registered records can also be located across claims using `review sources --identifier ID`.

## Record the novelty review

A review report includes a text field called `search_scope` identifying the collections, terms or other scope of the investigation. For a claim of sameness or distinction, also include `comparison` with the relevant source theorem and concrete mathematical differences.

```sh
research-multiplier review record-novelty CLAIM_ID not_found --report search.json \
  --actor "your name" --reason "Record the limits of the search"
research-multiplier review novelty CLAIM_ID
```

The `not_found` assessment keeps the result at `possibly_known`. Use `known` when reviewed originals cover the result, and pass each confirmed source with `--source SOURCE_ID`.

For `distinct`, record at least one completed [literature search](literature-search.md), link every compared original to a selected result and confirm its exact locator and conventions. Submit the source IDs, search IDs and current exact proof evidence already approved by a researcher. You can also use a `search_ids` array in the report.

```sh
research-multiplier review record-novelty CLAIM_ID distinct --report comparison.json \
  --search SEARCH_ID --source SOURCE_ID --evidence EVIDENCE_ID \
  --actor "your name" --reason "Compared the frozen result against the confirmed originals"
```

`human_cleared` records the researcher's scope-limited comparison. A withdrawn source or search archive, or proof evidence that needs rechecking, returns the judgment to `needs_review`. Prior distinct decisions without recorded search links also need review. The mathematical evidence keeps its own status.

## Export disclosure metadata

```sh
research-multiplier review disclosure EVIDENCE_ID --output exports/disclosure.json
```

Use the export as a starting point for a disclosure or a record to share. It includes stable proof and basis digests, linked model-attempt roles and recognized provider types, blind scope review records, human approval status and the digests of referenced searches and originals. It draws its AI-use counts from records linked to this evidence. Include any other relevant work in your own disclosure.

The JSON file's `sha256` hashes the sorted, UTF-8 JSON payload excluding that field, using the project's canonical JSON serialization. The same recorded state produces the same payload. The export uses an explicit set of metadata fields and omits local paths, statements, source excerpts, prompts, transcripts and model settings. Review the IDs and digests for the audience before sharing metadata from a restricted workspace.


A current interpretation question puts the effective claim status in
`awaiting_interpretation_review`. The review packet, evidence list, goal and
progress views show this hold alongside the last recorded claim status. A
historical proof and its review history remain available to inspect. Resolving
the question under the same scope restores the effective status of any still
current proof. Pending route conditions are local to their proof routes.

## Keep automatic work within review capacity

Set the policy once for the research database. This example pauses new automatic
work at five pending receipts, or after 60 minutes of recorded review time in
the last seven days.

```sh
research-multiplier review policy --max-pending 5 --weekly-minutes 60 \
  --require-understanding --actor Researcher --reason "Match my available review time"
research-multiplier review capacity
```

The automatic advancement worker and Lean search check remaining capacity before
starting work. Running jobs can finish and the verifier can still observe existing
results. Use `review policy` with only actor and reason to clear the limits.
Existing workspaces start without limits.

With the understanding requirement enabled, approval of a Lean or audited proof
requires a short explanation and takeaway. Submit these and optional measured
review minutes with the single normal approval command.

```sh
research-multiplier review-evidence EVIDENCE_ID approve --actor Researcher \
  --reason "Reviewed the proof" --explanation "The induction step uses the same invariant" \
  --lesson "The boundary case determines the admissible domain" --minutes 4
```
