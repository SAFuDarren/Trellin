# v0a production workflow

Use this workflow to prove the exact maximum-flow value of a fixed directed network. Draft and confirm the statement before running the default deterministic certificate check. You can explicitly select a model-assisted proof and independent cross-family audit. The final evidence stays open for human review.

## Set up PostgreSQL

Follow the [workspace and PostgreSQL setup](research-foundation.md#set-up-the-workspace). The default fixed-network route needs no model credentials.

## Configure two models

For the optional model strategy, set `RESEARCH_MULTIPLIER_PROVER_PROVIDER` and `RESEARCH_MULTIPLIER_PROVER_MODEL`, then set the corresponding `AUDITOR` variables for a different family. The provider adapter supports `openai`, `deepseek`, `anthropic`, `ollama`, `ollama-native`, and compatible endpoints. The default official endpoints use the corresponding provider's conventional key variable. For a custom endpoint, supply the role-specific `BASE_URL`, `FAMILY`, and `API_KEY` as applicable. Set the actual family for Ollama or compatible endpoints. Chat-compatible endpoints that support it can also receive the role-specific `REASONING_EFFORT` setting.

The native Ollama adapter sends bounded, non-streaming JSON requests to `/api/chat` and records the reported token counts. It provides a separate option from Ollama’s Chat Completions compatibility endpoint.

To use the Codex and Claude Code command-line tools, sign in with each tool first. Set the provider and model for each role. Replace the example model values with IDs available to your accounts.

```sh
export RESEARCH_MULTIPLIER_PROVER_PROVIDER=codex-cli
export RESEARCH_MULTIPLIER_PROVER_MODEL=CODEX_MODEL_ID
export RESEARCH_MULTIPLIER_AUDITOR_PROVIDER=claude-code
export RESEARCH_MULTIPLIER_AUDITOR_MODEL=CLAUDE_MODEL_ID
```

Set the role-specific `BINARY` variable if the executable is outside your search path. The worker runs in an empty working directory and receives no database DSNs. [Codex CLI](https://learn.chatgpt.com/docs/developer-commands?surface=cli) · [Claude Code CLI](https://code.claude.com/docs/en/cli-reference)

## Confirm the statement before proving

The review packet pairs the proposed statement and assumptions with structured definitions. Items already determined by the input are marked `stated`. Material questions are marked `needs_confirmation`. Check the packet before authorizing a proof run. The statement review uses PostgreSQL. The default program strategy generates and checks a certificate for the approved fixed network. Use `--strategy model` when you explicitly want a model-assisted candidate and audit.

```sh
research-multiplier formalization draft "/path/to/research/network.json" \
  --title "Maximum flow in a fixed network"
research-multiplier formalization show PACKET_ID
research-multiplier formalization confirm PACKET_ID --version PACKET_VERSION --hash PACKET_HASH \
  --actor "your operator name" --reason "Checked the statement and its scope"
research-multiplier formalization prove PACKET_ID
```

Substitute the packet's `id`, `version`, and `packet_sha256` from the CLI output. Confirmation binds the exact packet you reviewed. A complete input can be confirmed in one action. When a material item needs a decision, review its proposed text and add `--resolve ITEM_ID` for each accepted item. Proof evidence still goes through its own review. Re-running `prove` for the same confirmed packet and output directory reuses the existing work.

To include a material clarification at intake, pass `--clarify FILE` to `draft`. The file maps existing packet item IDs to questions. For example, use `{"H2": "Should allowable flows range over real values?"}` to surface the flow-value convention as a decision. `--resolve H2` accepts the text shown for that item in the packet. Review it before confirming.

If proof work pauses on a new material question, use `formalization show PACKET_ID` to read the question and its impact. If the existing statement already captures your intent, record that decision and retry the affected proof stage.

```sh
research-multiplier formalization resolve REQUEST_ID \
  --actor "your operator name" --reason "Keep the confirmed scope"
research-multiplier formalization prove PACKET_ID --retry
```

Use the question's `id` as `REQUEST_ID`. To change the intended statement, start a new draft with the revised input. A resolution records a decision about the existing frozen statement.

## Read the proof dependencies

An aligned proof lists the IDs of the premises it uses in `basis_items`. Eligible IDs come from the approved packet's definitions, hypotheses, and theorems, or from its checked intermediate statements. The fixed-network proof must account for the network (`H1`), feasibility (`H2`), and flow value (`H3`). The goal itself is the conclusion to prove.

The fixed-network checker records two intermediate statements, an exhibited feasible flow attaining the target and a cut bounding every feasible flow. After an exact check, `derivations.json` and `formalization show PACKET_ID` display each checked statement, its ID, its premises, and its verification record. The auditor receives the same statements and can compare them with the proof's declared basis. Review of the final evidence is recorded separately.

If the auditor is temporarily unavailable after the check, the checked statements remain available. Use `formalization prove PACKET_ID --retry` to continue with the saved proof and derivations. Pass the same `--out` value if you set one when starting.

To start with a fixed network and leave its attainable value open, see [bounded exploration](exploration.md).

## Run the model shortcut with an already reviewed input

```sh
research-multiplier v0a "/path/to/research/network.json" \
  --title "Maximum flow in a fixed network" \
  --record-freeze-as "your operator name"
```

Supply the network from your research workspace. An externally stored public network can use the same command.

The network JSON specifies `vertices`, `source`, `sink`, `edges` (tail, head, integer capacity), and `target_value`. The `v0a` shortcut takes an input you have already reviewed and records your confirmation through `--record-freeze-as`. The command starts the public model calls after recording the freeze. The PostgreSQL account identifies the service and the operator label is part of the freeze record.

The review packet includes the frozen statement, the candidate proof, the auditor's report, the flow and cut certificate, and the exact verification result. Use `run.json` to trace their hashes and linkage to PostgreSQL. A feasible flow and a matching cut certify the exact optimum for the specified network. The auditor and researcher review the proof text.

## Review exact evidence

The researcher records an approval or a return with a reason. A separate verifier operation applies an approved receipt.

```sh
research-multiplier review-evidence EVIDENCE_ID approve --actor "reviewer name" --reason "Scope and evidence checked"
research-multiplier promote-evidence EVIDENCE_ID --reason "Apply the approved receipt"
```

## Resume or replay a run

A failed attempt leaves its reason in PostgreSQL. Use `research-multiplier resume DIRECTORY` against the same database to continue an interrupted run. Use the directory printed by the proof command. The default location is listed by `research-multiplier research paths`. Add `--retry` to retry a recorded transient failure or inconclusive result. The existing `resume-audit DIRECTORY` command still retries an unfinished audit.

Use `research-multiplier replay DIRECTORY/run.json` to rerun the exact check against the saved statement and certificate offline. Keep the review packet and its `artifacts/` directory together.
