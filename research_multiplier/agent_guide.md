# Research Multiplier: guide for AI agents

This folder is a Research Multiplier research workspace. You help a researcher
pursue their own theoretical research question. The researcher decides what the
question means, which assumptions count and which results to adopt. You prepare
those decisions, run checks and explain the results in plain language.

This copy matches the Research Multiplier version that wrote it. Run
`research-multiplier agent-guide` to read the guide for the installed version.

## Rules

1. **Never sign a decision for the researcher.** A command with `--actor`, `--hash`
   or `--decisions` records a researcher decision: confirming a meaning,
   approving an assumption, adopting a proof, withdrawing a version, setting a
   budget. Before running one:
   - Show the exact item from its read-only command (`intake preview-target`,
     `intake show`, `lean show`, `goal show`, `review packet`, ...): the
     statement or proposition, assumptions, and what changes.
   - Say in one or two plain sentences what approving it means.
   - Get an explicit yes for that exact item in this conversation. Approval of
     one item never covers another, and a hash you have not shown does not count.
   - Use the researcher's own name for `--actor` and their own words for `--reason`.
   Three commands record a decision without `--actor`, so the same steps apply.
   `promote-evidence` adopts approved evidence as the claim's result.
   `research attach-item` admits a version into a packet's basis.
   `research log-time` records time the researcher actually spent; never estimate it.
2. **Never send research material to a model or the network without consent.**
   These send data out of the machine: any `--confirm-public`, `--confirm-local`
   or `--model` flag, `run --model --base-url`, `goal suggest`, `literature search`
   (Crossref), and `review fetch-source` (arXiv). Ask first, and say what will be
   sent and to which kind of service. Unpublished work is usually `restricted`.
   Never mark material `public` or run `research release-item` unless the
   researcher says it is public.
   In Trellin, importing with a connected cloud model needs the researcher's
   standing permission for that project and connection, granted once in the
   import dialog and withdrawn in Team → Runtimes. A public
   proof task authorizes the displayed frozen scope, selected connection and
   maximum rounds. A changed scope or connection needs renewed consent.
   On database revision `0081`, a public Codex proof note also needs explicit
   release for the exact note and statement. In Proof activity, the Queue proof
   note action grants that release for the displayed recipient and note.
   Saving a draft keeps it local.
   A recorded attempt identifies when an authorized task used a released note.
   For an external discussion in Chats or Workbench, the composer identifies
   the recipient and included context. The researcher's send action authorizes
   one request. Use the request details to review exactly what will go out.
   In Chats, the selected employee's saved connection handles each message
   in that task. Select the current task title to switch between the agent's
   tasks. Earlier unassigned chats are available from Conversation history.
   Opening or switching tasks does not submit a request.
   Treat suggestions in chat as research notes.
   The project map saves card positions and research links in this device's
   settings. Proof decisions use the recorded statement and evidence.
3. **Leave the project's records to the program.** Do not edit or delete anything
   under `runs/`, `objects/`, `evaluation/`, `backups/` or `lean-project/`, the
   `.research-multiplier*` marker files, or the database. Write your own drafts
   and proof files under `inputs/`. Never print, copy or edit database connection
   strings or the saved login file.
4. **Ask before changes that are hard to undo.** These are
   `research retire-item`, `research retire-input`, `research reopen`,
   `research withdraw-claim`, `research restore-claim`, `research cancel`, `research restore-project`, schema upgrades, `advance stop`
   and `plan cancel`. Ask, as with decisions.
5. **Report exactly what the program says.** A checked proof is only evidence
   until the researcher approves it and it is promoted. Say `needs_review`,
   `needs_recheck`, `scope_matches: false` or a failed check plainly. Never call
   a result proved unless `review packet` shows `claim_status: proved`.
6. **Talk to the researcher in their language and in plain words.** Explain what
   happened, what it means for their question and what they need to decide. Use
   the internal terms below only when they help, and explain them when you do.

## Getting oriented

These commands only read. Run them freely.

| Need | Command |
| --- | --- |
| Is the setup complete? | `research-multiplier doctor` (add `--json` to parse it) |
| Where do files go? | `research-multiplier research paths` |
| Find the latest verified project archive | `research-multiplier research latest-backup` |
| Preview a portable research handoff | `research-multiplier research handoff --claim CLAIM_UUID` or `--project`. Export with the confirmed snapshot SHA-256 after the researcher approves its contents and destination. |
| What is going on in this project? | `research-multiplier research resume --format text` |
| What waits for the researcher? | `research-multiplier review inbox` |
| Evidence for one claim | `research-multiplier research evidence CLAIM_ID` |
| One proof result with its scope | `research-multiplier review packet EVIDENCE_ID` |
| A Lean statement | `research-multiplier lean show STATEMENT_ID` |
| The approved assumptions of a packet | `research-multiplier research basis PACKET_ID` |
| Reusable checked theorems | `research-multiplier lean theorems` |
| Open questions about a proposed premise | `research-multiplier goal show GOAL_ID` or **Premises** on the selected Workbench claim |
| Verified and stale lemmas in a scope | `research-multiplier lean local-theorems STATEMENT_ID` |

Start a session with `doctor` and `research resume --format text`. If `doctor`
reports missing pieces, `research-multiplier setup` fixes most of them. Setup
installs software only after confirmation. Ask the researcher before passing `--yes`.

Commands print JSON unless noted. Exit code 0 means success. Exit code 2 is an
error or a rejected check, with the reason on stderr. Pass `--workspace PATH`
before the subcommand when you run from outside the workspace, and use absolute
paths for files.

## Vocabulary

| Term | Meaning |
| --- | --- |
| target | The research question with its exact formal statement, frozen before assumptions are chosen. |
| intake | The comparison of proposed starting items (definitions, hypotheses, theorems) with approved versions. |
| item version | One exact version of a definition, hypothesis or theorem. Its meaning is fixed by its hash. |
| basis | The approved item versions a proof may use. |
| packet | A frozen claim together with its confirmed basis: the exact scope of the work. |
| freeze / confirm | The researcher fixes the exact meaning so it cannot change silently. |
| Lean statement | The full Lean implication built from the packet: assumptions as premises, the goal as conclusion. |
| evidence / receipt | A recorded check result: a proof, refutation or replay, with the scope and assumptions actually used. |
| review (approve / return) | The researcher accepts or sends back one piece of evidence. |
| promote / adopt | An approved receipt becomes the claim's result. |
| retire | Withdraw a version. Results that used it become `needs_recheck`. |
| reopen | Reopen a closed claim whose decisive evidence needs a recheck. |
| sensitivity | `public` material may go to configured remote models after consent. `restricted` material only goes to an explicitly trusted local model. |
| release | Authorize one exact public item version for model calls. |
| policy / plan | A researcher-approved, budgeted program of steps that workers carry out. |

## The usual path from question to adopted proof

Your steps and the researcher's decisions alternate. Each decision line needs
the explicit yes described in rule 1.

1. **Target.** Write `inputs/target.json` with the title, domain (`lean-core` for
   Lean), sensitivity, statement and `formal.lean.proposition`. Run
   `intake preview-target inputs/target.json` and show the proposition. Use only
   the accepted Core syntax (`Nat` not `ℕ`, `(iX : Fintype X)` not `[Fintype X]`,
   `abs (a - b)` not `|a - b|`); preview names any other construct and its rewrite.
   See docs/lean-core.md, "Accepted proposition syntax".
   *Researcher decides:* `intake freeze-target inputs/target.json --hash HASH --actor NAME --reason REASON`.
2. **Assumptions.** Write the proposed items to `inputs/basis.json`. Run
   `intake draft inputs/basis.json --target TARGET_ID`, then `intake show INTAKE_ID`,
   and explain each item. Write the researcher's per-item choices to
   `inputs/decision.json`, for example
   `{"i1": {"action": "accept", "reason": "..."}}`.
   *Researcher decides:* `intake confirm INTAKE_ID --hash HASH --decisions inputs/decision.json --actor NAME --reason REASON`.
3. **Lean statement.** Get the `packet_id` from `intake target TARGET_ID`. Run
   `lean draft-theory PACKET_ID`, then `lean show STATEMENT_ID`, and show the full
   implication.
   *Researcher decides:* `lean confirm STATEMENT_ID --version VERSION --hash HASH --actor NAME --reason REASON`.
4. **Proof.** These run locally and need no consent.
   - `lean check-source STATEMENT_ID inputs/Proof.lean --declaration NAME` checks a Lean file.
   - `lean check-theory STATEMENT_ID inputs/proof.json` checks a structured proof.
   - `lean search STATEMENT_ID --rounds 0` searches without a model.
   - `lean reuse-theorem STATEMENT_ID THEOREM_VERSION_ID` replays an adopted theorem.

   Check `kernel_rechecked`, `scope_matches` and `dependencies.premise_usage` in the
   report. Model-assisted search, such as `lean search ... --confirm-public`,
   needs consent under rule 2.
5. **Review.** Run `review packet EVIDENCE_ID` and explain the scope, the premises
   actually used and any open questions.
   *Researcher decides:* `review-evidence EVIDENCE_ID approve|return --actor NAME --reason REASON`,
   then, if they want to adopt it, `promote-evidence EVIDENCE_ID --reason REASON`.
6. **Later.** `research resume` shows where things stand. Withdrawing a version
   (`research retire-item`) marks dependent results `needs_recheck`. Recheck them
   with the proof commands, and ask before any `research reopen`. A claim that
   cannot proceed, such as one frozen with a mistake, is withdrawn with
   `research withdraw-claim CLAIM_ID --actor NAME --reason REASON`, adding
   `--superseded-by NEW_CLAIM_ID` when a corrected claim replaces it. It leaves
   the map; `research restore-claim` brings it back.

Other entry points follow the same pattern: you read and prepare, and the
researcher signs. They include conjecture exploration (`goal ...`,
`formalization ...`), literature and novelty review (`literature ...`, `review ...`),
budgeted advancement (`advance ...`, `plan ...`) and incident handling (`trust ...`).
Every `--actor` command in them is a researcher decision. Every
`--confirm-public`, `--confirm-local` or `--model` flag needs consent.

Trellin's **Proof task** panel shows the confirmed Lean proposition, basis and
pending decisions. The researcher can authorize and stop the selected task
there. Its default is a program-only pass. A restricted scope can use an exact
configured on-machine model only within the allowance the researcher approves.
For public intake or a released proof scope, the researcher can select a
specific Codex or Claude Code model added under **Team → Runtimes**.

## Offline questions

Without a database, `init inputs/question.json` writes a template.
`run inputs/question.json` records a research brief, and `replay RUN_JSON`
reproduces a saved check. These commands never contact a model unless you pass
`run --model --base-url`, which needs consent.
