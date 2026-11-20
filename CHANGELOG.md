# Changes

## 1.0.40

- Reopen approved research questions and look up related work in the current project before preparing conjectures.

## 1.0.39

- Make the intake response format explicit so connected models can produce reviewable drafts.

## 1.0.38

- Show when a selected Claude Code model is unavailable for the configured account.

## 1.0.37

- Save and reopen private proof-note drafts for individual nodes, including while delivery is blocked.

## 1.0.36

- Review the approved basis and compare and confirm saved conjectures in Workbench.

## 1.0.35

- Return to the selected graph node from Review and open Settings or Help from the rail menu.

## 1.0.34

- Update the web development tools to address reported advisories.

## 1.0.33

- Propose and review a premise in Workbench, with explicit confirmation of a separate revised claim.

## 1.0.32

- Verify scoped local lemma plans in Workbench and show checked lemmas in the proof graph.

## 1.0.31

- Record repository handover verification and align guidance on candidate versions.

## 1.0.30

- Configure scheduled dependency updates for Python, the web interface, and GitHub Actions.

## 1.0.29

- Show whether each proof note is waiting, blocked, stale, or included in a proof attempt.

## 1.0.28

- Keep newly added proof goals on the active project canvas.

## 1.0.27

- Manage model connections from Workbench and Chats without losing the current draft or conversation.

## 1.0.26

- Show open issues in urgency and pickup order on the task board.

## 1.0.25

- Check the next package version against main before merging.

## 1.0.24

- Choose a specific Codex or Claude Code model in Team. Direct model conversations
  work in Chats before the first claim and in the graph beside the selected node.
- Send messages directly from a shared Workbench and Chats composer. Pick a
  model in the app, inspect request details when needed, and resume conversations
  when reopening the workspace.
- Browse conversations beside their message history, with a fixed input area.
  Team groups model connections, recorded activity, jobs, runtimes and usage.
  Both views use neutral gray workbench surfaces.
- Leave a node-specific draft in Proof activity. On database revision `0081`,
  release an exact public note for a later authorized Codex proof task and
  see the attempt that received it.
- Use **Back up and upgrade** in Proof activity to enable Team Codex guidance
  on an older Mac project after verifying its archive. Selected navigation
  rows and proof activity tabs use a flat gray background.
- Use a compact icon rail with keyboard and hover labels. Claim and section
  lists remain in the sidebar.
- Expand a Claims card in place, keep long graph labels inside their nodes, and
  see the recorded Library state in both the table and inspector.
- Drag the corner of a proof graph node to resize it horizontally and vertically.
- Screen readers can follow graph nodes to the inspector, read the overview
  resize range and current width, and navigate Help definitions.
- Edit an entry from the Library table or inspector and review both statements
  before confirming a new version. Frozen claims retain their original evidence.

- Add, test and remove user-selected Codex, Claude Code, OpenAI and Anthropic
  connections under Team → Runtimes. Detect installed Codex and Claude Code commands
  and reuse their existing logins. Store direct API keys in the macOS Keychain.
- Use a connected model to organize an explicitly reviewed public excerpt
  and save the result for the existing intake review.
- Authorize a selected connection for bounded proof work on a released
  Lean scope. Review the model and its reported usage alongside local verification.

## 1.0.23

- Document repository layout, task branch retirement, and issue pickup by urgency.

## 1.0.22

- Open a default Trellin library on first launch and keep new managed projects
  together under the user's application support directory.
- Move the library from the Mac app's Storage settings after confirming the
  destination and checking the copied files and database bindings. Keep the
  original copy available for recovery and leave external projects in place.

## 1.0.21

- Preview a project or selected claims and export a printable HTML and
  editable Markdown handoff from the same confirmed snapshot.
- Find the latest checksum-verified project backup through the existing
  backup and restore flow.

## 1.0.20

- Show the installed version in Trellin and offer a manual stable-release
  check. Optional daily checks share one setting across workspaces and
  stay off until enabled. Read release notes from the update notice.

## 1.0.19

- Introduce the Trellin workbench with English and Chinese getting-started
  pages, annotated proof-graph screenshots, and source installation links.

## 1.0.18

- Answer formalization questions in Trellin, prepare and inspect the exact
  revised Lean statement, and confirm it before moving on. Saved answers
  and proposals stay available after reopening the workspace.

## 1.0.17

- Add a documented v1 claim-list endpoint and generated types while keeping
  the previous claim list available. Versioned claim reads include a request
  ID and a log entry with route, status and duration.

## 1.0.16

- Attribute remaining human research decisions and policy records to the
  authenticated local researcher. The legacy review route also resolves the
  signed-in user after upgrade and accepts its prior payload.
- Database revision `0077` follows `0076` and preserves historical authorship.

## 1.0.15

- Update workbench views for clarifications, agent conversations and other
  committed workspace changes. While the complete stream is connected, the
  views update from events. Periodic refresh resumes during interruptions.

## 1.0.14

- Record clarification, agent activity, intake, library and other committed
  workspace changes in the existing resumable event stream.
- Database revision `0076` follows `0075`. Existing records and event cursors
  carry forward through the migration.

## 1.0.13

- Refresh open research views for confirmed scopes, withdrawals and proof task
  changes, and replay the same updates after reconnecting.

## 1.0.12

- Stream committed confirmations, withdrawals, proof task state and checked
  evidence from the workspace database, including work started from the CLI.

## 1.0.11

- Refresh open workbench views when evidence reviews are committed and replay
  missed review events after a connection interruption.

## 1.0.10

- Record new evidence reviews in the database transaction and expose a local
  event stream that resumes after the last received event.

## 1.0.9

- Record a stable local researcher ID on new approval records. The versioned
  workbench review endpoint resolves identity from the selected workspace and
  returns the original decision on a repeated request.

## 1.0.8

- Save stable claim and proof-node references in PostgreSQL. Renamed prefixes
  keep resolving to the original records, and activity displays recorded model
  reasoning effort.

## 1.0.7

- In Trellin, authorize proof work on a confirmed Lean scope, see its recorded
  progress and verification result, and stop the selected task.

## 1.0.6

- CI checks for new reverse imports between entry points and shared workflows.

## 1.0.5

- The full compatibility suite requires a separate installed wheel environment
  and rejects Python or package metadata inside signed Trellin builds.

## 1.0.4

- Document the release recovery drill and record a tested rollback between
  installed versions.

## 1.0.3

- Project restore checks the PostgreSQL connection format before writing to
  the target, so users can correct the configuration and retry safely.

## 1.0.2

- The Task link pull-request check uses the effective issue claim and rejects
  blocked or unclaimed work issues.

## 1.0.1

- Build and open Trellin on Apple Silicon Macs. Pick an existing research
  workspace and return to its last graph node when reopening the app.
- Trellin identifies the workspace and missing setup when a saved project
  cannot open, and offers another folder from the same window.
- Open a research folder in Trellin. On first open it sets up a `Trellin`
  subfolder with its own local PostgreSQL database.
- Trellin uses the Trellin logo for its app icon and on the screen shown while
  it opens a workspace; the workbench shows it as the browser tab icon.
- Organize selected project notes with an on-device model before the first
  claim, and enter a known Lean theorem or a target to prove by hand.

## 1.0.0

First public release of Research Multiplier, a local workspace for AI-assisted
theoretical research.

- Choose a research workspace, record an offline research brief, and replay
  supported deterministic checks without a model or database.
- Freeze an exact research statement and its approved basis. Use Lean 4.19.0
  and Mathlib to check structured proofs, `.lean` source files, or Lake
  declarations against the confirmed scope and record their actual dependencies.
- Save verified working lemmas, reuse applicable proofs within a workspace,
  and mark downstream evidence for recheck when an input is withdrawn.
- Advance supported proof routes under a budget with optional model adapters.
  Search local Formalpedia snapshots, use the research inbox, and resume work
  from the recorded checkpoint.
- Follow the separate first-proof walkthrough from installation through
  confirmation, verification, replay, and withdrawal.
- Open a local proof workbench (`workbench`) to inspect the dependency graph,
  exact Lean statements, open obligations, the impact of withdrawing an
  assumption, and live model activity, and to leave guidance for models. A
  side bar opens the review inbox (approve or return results), claims, the
  theorem library, model conversations, team runtimes and jobs, and
  automation policies. The interface is in English with a complete Chinese
  translation, chosen in Settings.
- `init-workspace` and `setup` write an `AGENTS.md` guide for coding agents into
  each research workspace.
- Setup can initialize a local PostgreSQL project through a Unix socket.
- Licensed under the Apache License 2.0. Contributions are accepted under the
  Developer Certificate of Origin; the project name and logo follow
  `TRADEMARKS.md`.
