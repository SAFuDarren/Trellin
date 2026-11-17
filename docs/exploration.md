# Explore a question before fixing the goal

You can start with a public fixed network and a set of existing mathematical statements. PostgreSQL keeps the approved statements and their structured versions together. The first approval confirms the starting basis and its meaning. Once you choose a conjecture, a separate approval confirms the exact goal that the proof will address.

The example below starts with an unknown maximum flow. It accepts your network and your existing definitions, hypotheses or theorems. Use an empty list for `items` if the built-in flow assumptions suffice.

```json
{
  "format_version": 1,
  "domain": "fixed-directed-max-flow",
  "sensitivity": "public",
  "title": "Maximum flow through a three-vertex network",
  "network": {
    "vertices": ["s", "a", "t"],
    "source": "s",
    "sink": "t",
    "edges": [["s", "a", 4], ["a", "t", 11], ["s", "t", 5]]
  },
  "items": [{
    "id": "D1",
    "kind": "definition",
    "version": "1",
    "source": "Public course notes, section 2",
    "natural": "The source side of a cut contains s and excludes t.",
    "formal": {"contains": "s", "excludes": "t"}
  }]
}
```

Each supplied item has a unique ID and a version/source pair. Use a stable public reference, such as a definition in a published worksheet or a theorem number. The optional `question` field asks for clarification of the exact statement in that item. The mathematical object in `formal` is structured JSON for review. Source references are recorded as given.

After [configuring PostgreSQL](research-foundation.md#set-up-the-workspace), save the example as `exploration.json` and run these commands. Substitute the values from the command output for `BASIS_ID`, `VERSION` and `BASIS_HASH`.

```sh
research-multiplier formalization explore-draft exploration.json
research-multiplier formalization show BASIS_ID
research-multiplier formalization confirm BASIS_ID --version VERSION --hash BASIS_HASH \
  --actor "your name" --reason "Reviewed the definitions and assumptions"
research-multiplier formalization explore BASIS_ID
```

Include `--resolve ITEM_ID` for each question whose stated interpretation you confirm. `explore` displays the reviewed basis and computes a candidate for this network. Here a cut of capacity nine supports the upper bound. The conjecture adds that the bound can be attained.

To discuss the result with the configured prover model, rerun `explore BASIS_ID --model`. That option sends the approved public network and mathematical statements, structured forms, source references and versions to the configured provider. Use public material in those fields. Questions and review reasons stay in PostgreSQL. The model proposes its own value from the approved input. The output reports whether it agrees with the computed cut, and includes the model's proposed next check. Selecting the reviewable candidate uses the computed value.

To choose the conjecture, use the candidate ID `maximum-flow`.

```sh
research-multiplier formalization choose BASIS_ID --candidate maximum-flow \
  --version VERSION --hash BASIS_HASH
research-multiplier formalization show GOAL_ID
```

The new goal draft references the original basis. Its proof uses the three fixed-network hypotheses identified in the candidate's `basis_item_ids`. Extra source items remain available in the exploration record. Confirm the chosen goal using its own version and hash from `show`. The matching hypotheses carry forward from the reviewed basis.

```sh
research-multiplier formalization confirm GOAL_ID --version GOAL_VERSION --hash GOAL_HASH \
  --resolve goal --actor "your name" --reason "This is the conjecture I want to prove"
research-multiplier formalization prove GOAL_ID
```

For the proof and audit outputs, see the [reviewed proof workflow](v0a.md#confirm-the-statement-before-proving).
