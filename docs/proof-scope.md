# Check a proof against the intended statement

Use `formalization show` to read the exact frozen version of the goal,
assumptions, and definitions. To compare it with a structured extraction, put
the observed scope in a JSON file with an `items` array. Each item has an
`id`, a `kind`, and a `formal` object in the same representation as the
frozen packet. Include all the formalisms that the evidence claims to cover.
Keep the order of quantifiers and other ordered terms.

```sh
research-multiplier scope compare PACKET_ID observed.json
```

The comparison places each structural change at its exact path. Changed
variables, missing conditions, additional assumptions, and changed
quantifier order are visible even if the surrounding wording is unchanged.
The comparison treats formal syntax as exact. When two formalizations differ
syntactically, any mathematical equivalence is a separate proof obligation.

## Read a derivation independently

For a public prose proof with an archived attempt, use the configured
`AUDITOR` adapter from a different model family than the author. If the
proof has a statement preamble, place the argument under a Markdown
`# Proof` heading when creating the proof. The software sends the argument
section and removes numbered premise labels. It sends neither the original
frozen claim nor its formal packet as separate inputs. Assumptions and
conclusions that occur within the derivation remain available for scrutiny.
Use published examples or explicitly public material.

```sh
research-multiplier scope blind EVIDENCE_ID --confirm-public
research-multiplier scope show EXTRACTION_ID
```

For an older run outside your usual workspace, use `--run-directory` with
its original path. The record binds the original proof hash, exact submitted
derivation, model identity, and findings. Reports are linked from
`review packet`. Raw transcripts remain in the workspace archive. A blind
extraction is advisory and can surface doubts for the researcher. The
procedure for verification and a separate human decision on evidence is the
same for proofs with or without a blind extraction.

## Check a counterexample against the original target

A registered exact checker must use the original frozen contract to verify a
counterexample certificate. A Lean refutation checks the exact negation of
the entire frozen proposition, for example `¬(H₁ → H₂ → P)`. It can establish
refutation logically while leaving the witness field empty. Failed attempts
and witness candidates outside the frozen assumptions stay in the
investigation history. The review inbox and packet surface current
evidence of refutation for a human decision. Accepting it does not revise the
original assumptions. Review a changed scope as a new target or version.

## Review the Lean statement itself

```sh
research-multiplier scope lean-statement STATEMENT_ID
research-multiplier scope lean-statement STATEMENT_ID --backtranslate-public
```

The first command uses no model. It places the original formalization packet, exact Lean
propositions and environment imports side by side, with cues for natural-number arithmetic
and sign-discarding conversions. Cues are prompts for inspection based on the written syntax.
The explicitly public option asks the configured auditor to read the formal side alone and
archives its answer against that exact statement hash. The researcher confirms the Lean
statement through the existing approval command.
