# Local proof workbench

Open the graph of a research project from your own browser. Start the workbench with the installed Research Multiplier command and an initialized [project directory and database](research-foundation.md#set-up-the-workspace).

```sh
research-multiplier --workspace "/path/to/my-research" workbench
research-multiplier --workspace "/path/to/my-research" workbench CLAIM_UUID
```

Use `--no-browser` to open the printed session link yourself. Keep that link local and stop the process with **Ctrl+C** when you are done. The workbench listens on the loopback address and uses the project's existing `RM_HUMAN_DSN` on the server. The browser sends project reads and, when enabled, advisory notes to the local service. Existing workers run Lean and use their signed model routes.

The graph and activity view require database revision `0069`. Local-model guidance requires revision `0070`. Public Codex guidance requires revision `0071`. For an existing project, [back up the workspace](research-foundation.md#recover-and-monitor) and run the normal [migration command](research-foundation.md#set-up-the-workspace) only when you choose to enable the graph. The launcher leaves the database schema as it finds it.

## Start a research project

**Organize with AI** and **Add theorem** are available before the first claim. See [Project intake and theorem entry](project-intake.md) for local model setup and human review.

## Read a graph

- **Proof** shows the frozen goal, checked steps, reductions and open obligations. Choose a node to see the stored statement and its current status. Long Lean statements start folded. The fold opens the exact stored text, including any binders already in the record.
- **References** distinguish recorded proof uses from confirmed scope premises and planned edges. The source and version identify the record behind each node. Archived or stale proof evidence retains its status.
- **If withdrawn** previews the consequences of withdrawing the selected approved version. It uses the same dependency closure as the withdrawal command. Existing evidence requiring recheck, affected obligations, and frozen scopes requiring review appear separately. It does not evaluate a new assumption or determine whether a theorem would become false.

Some projects have no Lean statement for a record. The workbench shows its recorded wording and indicates the Lean text is unavailable. It does not invent a formula or infer a mathematical field from the checker name.

The narrow bar on the far left switches between sections: **Workbench**, **Review** (results and questions waiting for you, with approve and return), **Claims** (every claim by status), **Library** (theorems and hypotheses, and where each is used), **Chats** (model conversations across claims), **Team** (runtimes, model agents, usage and jobs), and **Automation** (advancement policies). Counts on the bar show items waiting for review and jobs running now. **Help** and **Settings** sit at the bottom of the bar.

The workbench has four areas. The left column lists the project's claims by domain, with a search field. The centre shows the selected claim's graph, with the conversation for the selected node below it. The right column lists records awaiting your review, or the records affected in **If withdrawn**, above the details of the selected node. Drag any border between areas to resize it, or focus a border and use the arrow keys. The sizes are remembered in this browser; **Settings** can reset them. The interface is in English by default; choose **简体中文** in **Settings** to switch to Chinese, and the choice is remembered too. The map can be dragged by its title and resized from its corner.

Drag the graph to pan it. Pinch, or scroll with Ctrl or ⌘ held, to zoom around the pointer. The buttons in the corner zoom, return to 100% and fit the graph to the window; with the canvas focused, the arrow keys pan, + and − zoom and 0 fits. The small **Global position** map in the corner shows the recorded nodes of recent claims, with a dashed circle at the current claim.

## Work with the models

Every node has a short code made of the claim's initials and a number, for example `FLT-09`, so you can refer to it in notes and discussions. A node whose recorded job is running shows the agent (model and reasoning effort) and the number of parallel jobs, for example `GPT-6.2 Sol xhigh ×2`. A queued job shows a clock. The legend under the graph counts nodes by status and running or queued jobs. A claim-level marker covers work whose recorded job has no exact proof-node reference.

The conversation under the graph shows your notes, the models' recorded methods and reasons, and the Lean results for the selected node. Choose **All nodes** to see the whole claim, with a link from each entry back to its node.

Select a node, write a short method or focus suggestion, choose the recipient, then send it. This attaches the note to the selected claim and exact confirmed Lean statement. For a private claim, the next otherwise eligible request from a signed private-local model policy can read up to three pending notes. The ordinary [advance worker](research-progress.md#advance-a-confirmed-lean-statement) still governs scheduling, budget, and proof verification. If no eligible call runs, the message remains pending. A recorded model attempt acknowledges the note. Existing graphs remain usable on `0069` and local-model guidance remains usable on `0070`.

Each note is local to the project and eligible on its original statement version.

## Send a public note to Codex

Use a public project with a confirmed Lean scope, a released basis and an approved Lean advancement policy with model rounds available. Configure the existing [Codex CLI adapter](model-adapters.md#choose-a-model-for-each-task) as the worker's Lean author.

```sh
export RESEARCH_MULTIPLIER_LEAN_AUTHOR_PROVIDER=codex-cli
export RESEARCH_MULTIPLIER_LEAN_AUTHOR_MODEL=YOUR_CODEX_MODEL
research-multiplier --workspace "/path/to/my-research" advance worker --policy POLICY_UUID --watch 5
```

In the workbench, select a node, choose the **Codex** recipient (for example `FLT-09 · Codex · GPT-6.2 Sol xhigh`) and send the note. The send button then asks for a second click, which confirms that this individual note may be sent to Codex with the released scope. The next eligible Codex request reads it within the signed budget. The note stays bound to the selected Lean version. The running job names the model and links to the selected node. A recorded response appears in the conversation alongside the separate Lean verification result.

Private claims expose only the local guidance channel. Codex requests require public authorization for the exact statement, existing proof context and individual note. The same worker still governs scheduled retries and verifies candidates through Lean.
