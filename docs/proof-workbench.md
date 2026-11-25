# Local proof workbench

Open the graph of a research project from your own browser. Start the workbench with the installed Research Multiplier command and an initialized [project directory and database](research-foundation.md#set-up-the-workspace).

```sh
research-multiplier --workspace "/path/to/my-research" workbench
research-multiplier --workspace "/path/to/my-research" workbench CLAIM_UUID
```

Use `--no-browser` to open the printed session link yourself. Keep that link local and stop the process with **Ctrl+C** when you are done. The workbench listens on the loopback address and uses the project's existing `RM_HUMAN_DSN` on the server. The browser sends project reads and, when enabled, advisory notes to the local service. Existing workers run Lean and use their signed model routes.

The graph and activity view require database revision `0069`. Local-model guidance requires revision `0070`. Public Codex guidance requires revision `0071`. For an existing project, [back up the workspace](research-foundation.md#recover-and-monitor) and run the normal [migration command](research-foundation.md#set-up-the-workspace) only when you choose to enable the graph. The launcher leaves the database schema as it finds it. Proof tasks with a selected public connection require revision `0078`.

## Start a research project

**Research actions → Organize with AI** and **Add theorem** are available before the first claim. See [Project intake and theorem entry](project-intake.md) for local model setup and human review.

## Switch between the graph and Review

The rail has five spaces: **Home**, **Work**, **Chats**, **Team** and **Automation**. **Home** opens the **Project map** beside the rail. **Work** opens on the claims board, and its panel lists **Claims**, **Inbox**, **Review**, **Agent tasks** and **Library**. Choose **Work → Review** for items awaiting review. The **Work** badge shows how many are waiting, and the graph keeps its selected claim and node while you switch.

In the Mac app, use the project tile at the top of the rail to switch projects. The **Project map** shows the project's claims on one canvas. Click a card or choose **Find claim** to select it and read its statement, Lean text, and review items in the right-hand details panel. Use **Details** to toggle the panel. Double-click a card, press Enter while it has focus, or choose **Open proof** in the inspector to show the proof graph on the main canvas. **Proof task** appears first in the inspector. The **More inspector actions** menu holds actions that cannot fit beside it. Use **Proof**, **References** and **If withdrawn** to explore the graph, then select a node to read its details. In a narrow window, Escape first closes the details drawer. Press Escape again or click **Project map** to return to the same map position and selection. The map footer shows the connection status next to the zoom controls.

Choose **Global map** to show the existing overview of recent claims and their recorded proof dependencies in the current workspace. Move it by its title, resize it from the corner, or choose a claim in the overview to bring its card into view.

Drag a card to move it. To move it with the keyboard, focus the card, open its context menu and choose **Move card**. The arrow keys preview the move, Enter saves it and Escape cancels it. To record a research relationship, point to a card and drag its bottom handle to another card. With a card focused, press **L**, use the arrow keys or Tab to reach the other card, and press Enter. Escape cancels. Over an eligible card, the preview shows the saved route and marks the two points where the line will attach. Select a line to see both claim names and use **Remove** or Delete to remove it. Use **Undo** in the confirmation to restore the link. The **Research links** menu also lists saved links. Positions and research links are saved per project in this device's settings.

**Help** and **Settings** are at the bottom of the rail. Each opens in one click.

## Start a proof task

Select a claim on the map and open **Proof task** from its inspector. The panel shows the confirmed Lean proposition, its basis, and any decision that blocks work. If the Lean statement still needs preparation or review, follow [the Lean scope review](lean-core.md) before starting.

When the scope is ready, confirm it in the panel and record your reason to start. The worker runs deterministic checks and proof reuse first. The default allows zero model rounds. For restricted research, the panel can authorize a bounded number of rounds with the exact configured private Lean model once that route is available. For a public goal with a released scope, [add a connection](project-intake.md#connect-a-model) in **Team → Runtimes**. Select it in **Proof task**, set the maximum model rounds, and check the theorem and released basis before allowing the send. The authorization covers that connection, scope and cap. A changed scope or connection needs a new authorization.

The panel shows the proof task state, selected model, reported model usage, open obligations when the worker reports them, and the verified receipt when one is issued. The existing [public Codex note flow](#send-a-public-note-to-codex) is also available. Review and adoption remain researcher decisions. **Stop** ends the selected policy and prevents new work on it. Reopen the same workspace and proof task to see the recorded checkpoint. If an authorized pass was interrupted, use **Continue** to return to the same policy.

## Verify local lemmas

Select a claim with a confirmed Lean statement and choose **Research actions → Local lemmas**. The panel shows the exact destination and statement version. Paste a version 1 [structured lemma plan](lean-core.md#derive-and-reuse-local-lemmas) containing intermediate nodes, then choose **Verify lemma plan**. Use the same local Lean runtime and project database configuration as `lean derive`. The proof graph shows each verified lemma, including any that passed before a later node failed.

For example, if the frozen scope assumes `True` and has the original goal `True ∧ True`, this plan derives an intermediate lemma directly from the premise.

```json
{"format_version":1,"nodes":[
  {"name":"Helper","proposition":"True","proof":{"intro":{"count":1,"body":{"use":0}}}}
]}
```

Complete the frozen goal through the existing proof workflow.

## Propose a premise

Choose **Research actions → Premises** in the selected Lean proof. Enter the proposed wording, its exact Lean proposition, the question to decide, and the expected impact. The pending item appears beside the current proof without adding a dependency. The interpretation review pauses new proof work on that scope. Reopen the project to continue the same review.

Review the target, approved starting basis, and proposed premise before recording your reason. Reject the proposal to keep the current basis. Confirm the revised claim to approve the exact premise and freeze a separate claim with the same conclusion. The original claim retains its statement and proof history. Open the revised claim from the decision and use the ordinary [Lean statement review](lean-core.md) before proving it. Other pending interpretation questions must be resolved before confirming a revision. See [the condition workflow](research-goals.md#handle-a-questioned-condition).

## Read a graph

- **Proof** shows the frozen goal, checked steps, reductions and open obligations. Choose a node to see the stored statement and its current status. Long Lean statements start folded. The fold opens the exact stored text, including any binders already in the record.
- **References** highlights recorded proof uses with solid lines and allowed premises with dotted lines. Planned steps appear as quieter dashed lines. The edge legend follows the current view. The source and version identify the record behind each node. Archived or stale proof evidence retains its status.
- **If withdrawn** previews the consequences of withdrawing the selected approved version. It uses the same dependency closure as the withdrawal command. The legend and inspector count each affected record once, including records from other claims. The inspector lists the selected premise separately and links to any of these records drawn in the open proof. Actual withdrawal uses the existing separate command.

Some projects have no Lean statement for a record. The workbench shows its recorded wording and indicates the Lean text is unavailable. It does not invent a formula or infer a mathematical field from the checker name.

In **Work → Claims**, browse claims by domain and status, or choose **Display → Status board**, **List** or **Table**. **Work → Library** shows theorems and hypotheses and where they are used. **Help** and **Settings** sit at the bottom of the rail.

On database revision `0073` or later, **Review** records the local researcher's ID from the bound workspace for each new evidence decision and shows **Recorded by** with their name, so you do not type a name. **Add theorem** and the paper import confirmation sign the same way. Enter the reason and choose **Approve** or **Return**. Workspaces with an older database still use the earlier review form, which asks for your name. Previous decisions retain their recorded names.

Database revision `0077` also records the bound researcher's ID on new clarification decisions, plan cancellations, literature confirmations, and other human-authored records. Entries made before the upgrade retain their existing attribution.

Database revision `0074` adds a workspace event stream for evidence reviews. An approval or return from the workbench or CLI records an event with the committed decision. Clients can resume from a prior event ID. The event carries identifiers and the decision type. Review reasons stay in the research record.

Database revision `0075` also records confirmed claims and premises, confirmed Lean statements, retirements of premises and verification inputs, proof task activity, new evidence, and adopted conclusions. Clients can use these events to refresh existing views. The dependency rules still determine which proofs need recheck, including claims that reused a retired result. Event frames carry IDs and operation types, without exposing proof text or review notes.

With the live workbench, approving or returning evidence from the CLI updates the Review list, the selected claim's graph and the workspace overview in an open window. After a connection drops, the workbench resumes after the last event it applied. On revisions `0074` and `0075`, periodic refresh also covers operations outside those revisions' event coverage.

With revision `0075` and the updated workbench, confirmed claims and premises, proof task changes, and withdrawals also refresh the affected views. A withdrawn dependency may affect multiple claims. The workbench re-reads their current assurance status, including proofs that must be checked again. The proof task dialog shows active CLI worker leases and the recorded verification result.

With revision `0076` and Research Multiplier `1.0.15` or later, clarifications,
agent guidance and replies, intake changes, library changes and other
workspace writes update the relevant views. While the event stream is
healthy, these views refresh on events. During an interruption they check
for changes periodically, then replay missed events when the connection
returns. Older databases keep the earlier refresh behavior. Agent text and
clarification answers remain in the workspace and are fetched through the
existing authorized views.

The selected claim or proof node appears in **Proof details** to the right of the canvas, below records awaiting review. Choose **If withdrawn** to see affected records. Drag the inspector border to resize it, or focus the border and use the arrow keys. Panel sizes are remembered in this browser and **Settings** can reset them. Choose **简体中文** in **Settings** to switch the interface language.

Opening a proof fits small graphs and starts large graphs at a readable zoom around the selected node. Use **Fit to window** in the corner to see the entire graph. Drag to pan. Pinch, or scroll with Ctrl or ⌘ held, to zoom around the pointer. With the canvas focused, the arrow keys pan, + and − zoom and 0 fits.

Select a node to read its full formula and Lean statement in the inspector. Running jobs appear as a spinner and count inside the node. With reduced motion enabled, the spinner stays still. The card outline marks selection. Hover over the activity badge or check the inspector for the recorded Agent and model. Open **Edge styles** to look up connection types. The footer shows labelled status counts. When the labels need more than two rows, open **Status** to read every count. Hover, focus or select a node to reveal its resize handle. Drag the handle or focus it and use the arrow keys.

## Assign project work

With database revision `0083`, open **Team → Agents** and choose **Assign task**. Give the agent an outcome and acceptance criteria. The workbench previews the selected agent, its model connection, the allowed operations, and the exact input. Confirm before starting. The project needs its configured scheduler login to dispatch work.

For an implementation task, choose an independent Git checkout under the project's `inputs/` directory, a full commit ID, and the exact files to change. The agent proposes text replacements. Trellin builds a patch and checks that it applies to the recorded inputs in a temporary directory. The patch is a delivery for review. The apply check measures patch applicability.

Choose **Assign review** on a delivery to send that version to another agent. Drafts and code patches use the same review flow. **Work → Agent tasks** shows queued work, failures, delivered artifacts, and human decisions. Open a delivery to accept or return that exact version. Failed or interrupted work requires a new confirmation to retry the recorded input. Pending deliveries remain in the project after restarting the app.

## Work with the models

With database revision `0079` or later, use **Chats** to start a workspace
discussion before creating a claim. Select an agent saved in **Team** and
name a new task. The header shows the agent and current task. Click the task
title to switch among that agent's tasks and see which ones are waiting for a
reply or have failed. Use the arrow keys to move through the list, Enter to
select a task and Escape to close it. Drafts stay with their task while you
switch in Chats. A waiting turn shows its elapsed time from acceptance. The
agent and task link are stored with this project's settings on this device.

Use the **Conversation history** clock beside the Chats heading to find
earlier unassigned conversations and proof activity. Saved conversation and
proof activity links open the same records. An earlier conversation attached
to a proof node still shows its link to the node.

Open **Agent chat** from the bottom of any page and choose a saved Agent or model. In **Workbench**, use **Ask an Agent** from the selected claim or proof node's inspector to attach that context. Choose a recipient and open the conversation. Collapse the panel to return to it later, or choose **Open full conversation** to continue in Chats. **Agent activity** shows queued and running work and results to review or retry. The composer identifies the destination and included context,
including recent turns and, when selected, a node excerpt.
Check **Send without earlier messages** to exclude prior turns while keeping the conversation and any attached node.
Open **Message context → Request details** to inspect the exact contents.
The send button authorizes and submits that request in one step. Project files
are outside that payload.
The conversation and attribution are saved with the project. Removing a
conversation hides it from the active list and preserves its history.

Expand **Proof details → Proof activity**, select a proof node and use **Save draft** to keep a
private note in the project. Saving works while proof guidance is blocked,
including before the Lean statement is confirmed. Reopen the same node to
continue editing the saved draft or discard it. To make a draft available to an
eligible proof task, use **Queue proof note** once the panel shows an available
recipient and explains how the note may be used. Saving a draft requires
database revision `0082` or later.

On database revision `0072` or later, a node reference such as `FLT-09` uses a saved claim prefix and a number assigned once. Use **Research actions → Research references** to change a claim prefix or open a reference from your notes. The old prefix continues to resolve to the same claim and node. Choose an optional claim prefix when adding a goal by hand, or accept the program's default. Earlier databases show temporary graph labels until you run the normal workspace upgrade.

A node whose recorded job is running shows the agent and the number of parallel jobs. The workbench also displays reasoning effort when the model attempt records it, for example `GPT-6.2 Sol xhigh`. A queued job shows a clock. The legend under the graph counts nodes by status and running or queued jobs. A claim-level marker covers work whose recorded job has no exact proof-node reference.

**Proof activity** shows your notes, the models' recorded methods and reasons, and the Lean results for the selected node. Choose **All nodes** to see the whole claim, with a link from each entry back to its node.

Select a node, write a short method or focus suggestion, choose the recipient, then select **Queue proof note**. This attaches the note to the selected claim and exact confirmed Lean statement. For a private claim, the next otherwise eligible request from a signed private-local model policy can read up to three pending notes. The ordinary [advance worker](research-progress.md#advance-a-confirmed-lean-statement) still governs scheduling, budget, and proof verification. If no eligible call runs, the message remains pending. A recorded model attempt acknowledges the note. Existing graphs remain usable on `0069` and local-model guidance remains usable on `0070`.

Each note is local to the project and eligible on its original statement version.

## Send a public note to Codex

If the Mac workbench shows a database upgrade is required, select **Back up and
upgrade** in **Proof activity**. Confirm the project in the native dialog. The
app reopens the same project after verifying its backup and applying the bundled database upgrade.

On database revision `0081` or later, open **Proof activity** for a node with a
confirmed public statement and shareable basis. Select the recipient and use
**Queue proof note** or Enter to release this note for a later authorized attempt.
Start a bounded **Proof task** with a
Codex connection from Team. The task checks its signed model, statement,
basis and budget before using the queued note. The status beside each note tells you whether it is waiting for an authorized
proof task, blocked, tied to an older statement, or included in an attempt.
Inclusion appears when the task records a receipt.

The terminal-started workflow is also available. On earlier database
revisions use it to deliver released guidance.

Use a public project with a confirmed Lean scope, a released basis and an approved Lean advancement policy with model rounds available. Configure the existing [Codex CLI adapter](model-adapters.md#choose-a-model-for-each-task) as the worker's Lean author.

```sh
export RESEARCH_MULTIPLIER_LEAN_AUTHOR_PROVIDER=codex-cli
export RESEARCH_MULTIPLIER_LEAN_AUTHOR_MODEL=YOUR_CODEX_MODEL
research-multiplier --workspace "/path/to/my-research" advance worker --policy POLICY_UUID --watch 5
```

In the workbench, select a node, choose the **Codex** recipient (for example `FLT-09 · Codex · GPT-6.2 Sol xhigh`) and queue the note. This action releases the displayed note for Codex use with the released scope. The next eligible Codex request reads it within the signed budget. The note stays bound to the selected Lean version. The running job names the model and links to the selected node. A recorded response appears in **Proof activity** alongside the Lean verification result.

Private claims expose only the local guidance channel. Codex requests require public authorization for the exact statement, existing proof context and individual note. The same worker still governs scheduled retries and verifies candidates through Lean.
