# Get started with Trellin

[简体中文](quickstart.zh-CN.md) · [Documentation](README.md)

## Install

For the macOS app, see [Trellin for macOS](desktop.md). For the Research Multiplier CLI, install from source with Git and Python.

Use Python 3.10 or later. Create your Python environment outside the software checkout. On macOS or Linux:

```sh
RM_ENV="$HOME/.venvs/research-multiplier"
python3 -m venv "$RM_ENV"
. "$RM_ENV/bin/activate"
```

Install the CLI from the repository.

```sh
python -m pip install "git+https://github.com/SAFuDarren/Trellin.git"
research-multiplier --help
```

If you already have a wheel for the version you want, pass its local path to `python -m pip install`.

If you are working from a Git checkout, build from a committed source snapshot so build files stay outside the checkout. Run this from the checkout with the environment above active.

```sh
RM_BUILD_DIR="$(mktemp -d)"
git archive --format=tar.gz --prefix=research-multiplier/ \
  --output="$RM_BUILD_DIR/research-multiplier-src.tar.gz" HEAD
python -m pip install "$RM_BUILD_DIR/research-multiplier-src.tar.gz"
```

On Windows, the offline workflow can use PowerShell. Invoke the environment's Python directly.

```powershell
$RmEnv = Join-Path $HOME ".venvs\research-multiplier"
py -3 -m venv $RmEnv
$RmPython = Join-Path $RmEnv "Scripts\python.exe"
& $RmPython -m pip install "git+https://github.com/SAFuDarren/Trellin.git"
& $RmPython -m research_multiplier --help
```

## Start with your own question

In the local proof workbench, use **Research actions → Organize with AI** to review selected notes before creating a claim, or **Add theorem** to enter a theorem yourself. See [Project intake](project-intake.md).

Choose the project folder when prompted. The first command prints its actual path. In the remaining commands, replace `PATH` with that folder and run from there.

```sh
research-multiplier research init-workspace
cd "PATH"
research-multiplier research paths
research-multiplier init inputs/question.json
```

In PowerShell, use `& $RmPython -m research_multiplier` in place of `research-multiplier` and `Set-Location "PATH"` to change directories.

Open `inputs/question.json` in a text editor. Fill in the three text fields `title`, `question`, and `success`. Keep `kind` set to `open` to record a question in your own words.

```sh
research-multiplier run inputs/question.json
```

An open question returns `needs_review`. The command prints the paths to `report.md` and `run.json` under `runs/offline/`. Read the report, or pass the printed `run.json` path to `research-multiplier replay` to reproduce the saved check. To try an exact check, see the [graph coloring and linear-program input formats](problem-formats.md#built-in-checks).

In a project bound to PostgreSQL, use `research latest-backup` to find a verified project archive. To print or continue your research outside Trellin, [preview and export a handoff](research-foundation.md#take-a-research-handoff-with-you) for the whole project or selected claims.

For a known path, use `research-multiplier research init-workspace "PATH"` to select it explicitly. From another directory, pass `--workspace "PATH"` before the subcommand and use absolute paths for project files. [Workspace layout](research-foundation.md#workspace-paths)

## Verify and reuse a first Lean proof

Start with the small proof in the [Lean guide](lean-core.md). It shows how to confirm a statement and check it with zero model calls. The same guide covers replay and withdrawal. If you have a matching release bundle, check its `first-proof` archive against `SHA256SUMS`, unpack it outside the checkout, and follow `first-proof/README.md` for the complete walkthrough.

For this walkthrough, use native Lean 4.19.0 and PostgreSQL 18 with pgvector on macOS or glibc Linux. Use a fresh project folder and an empty database for the example. Follow the setup guides to install PostgreSQL support and bind the workspace. [Database setup](research-foundation.md#set-up-the-workspace) · [Native Lean setup](lean-core.md)

To prepare a project folder for persistent research and Lean in one step, run `setup` with the folder. It uses your existing PostgreSQL 18 server with pgvector, and installs or downloads anything missing only after you confirm. [What setup does](research-foundation.md#set-up-in-one-command)

```sh
research-multiplier setup "PATH"
```

To see what is still missing, run `doctor` from the project folder. It only reads your setup. Each line that needs attention ends with the command or setting that fixes it, and the summary shows whether offline work, persistent research and Lean checks are ready. Add `--json` for scripts and agents.

```sh
research-multiplier doctor
```

To work with an AI coding agent such as Claude Code or Codex, start it in the project folder. `research init-workspace` and `setup` write an `AGENTS.md` guide there, plus a `CLAUDE.md` that points to it. The guide tells the agent which commands only read, which ones send material to a model, and that every decision you sign stays yours. An edited copy is never overwritten. `research-multiplier agent-guide` prints the current guide, and `agent-guide --install` refreshes an unedited copy after an upgrade.

## Choose your next workflow

- [Set up an existing or new research database](research-foundation.md#set-up-the-workspace) for persistent research scope, evidence and model budgeting.
- [Recover a project archive](research-foundation.md#recover-and-monitor) using a PostgreSQL URL and a fresh target database. If a later step fails after import, keep the imported database and the archive, then retry with a fresh database and an unused directory.
- [Verify a Lean proof in your own project](lean-core.md#check-a-lean-source-file-or-compiled-declaration) and see the confirmed assumptions and facts actually used.
- Use **Home** to open the graph and **Switch view** in the sidebar header to open Review. The bottom rail menu opens Settings and Help. [Workbench navigation](proof-workbench.md#switch-between-the-graph-and-review)
- In the Mac app, choose a registered project from **Projects** in the Workbench sidebar. The project name in the workbench header also opens the project switcher. The sidebar starts folded when the Mac library has one registered project. On the **Project map**, select a claim to read its details on the right and use **Open proof** to read the graph on the main canvas. Double-click a card or press Enter while it has focus to open its proof. Press Escape or choose **Project map** to return to the same position and selection. **Global map** opens the overview of recent claims and their proof dependencies. Use **Find claim** to locate a claim. Drag cards to arrange them. Drag a card's bottom handle or press **L** on a focused card to record a research relationship. Select a line to remove it. Positions and links are saved on this device per project.
- In **Review**, open **Open research goals** to inspect an approved goal before preparing a candidate. Search local sources and claims from **Related work**. [Local goal search](research-goals.md#find-related-work-in-the-project)
- In **Review**, open **Conjectures to compare** to compare already prepared Lean candidates and the approved basis before confirming a claim. [Conjecture review](research-goals.md#reopen-a-prepared-conjecture-in-workbench)
- Open [Proof task in Trellin](proof-workbench.md#start-a-proof-task) to review the confirmed Lean scope, authorize proof work, and monitor its result.
- Open **Premises** on a Lean claim to propose a premise and see it on the current graph. Review the exact basis before confirming a separate revised claim. [Premise review](proof-workbench.md#propose-a-premise)
- In the Mac app, add your installed Codex or Claude Code from [Team → Runtimes](project-intake.md#connect-a-model) and select its model. Choose that connection for a public intake excerpt or for a released proof scope on database revision `0078`.
- In **Team → Agents**, choose **Add agent**, select a saved model connection, and enter a name and optional role settings. The profile belongs to the current project on this device. Use **Edit** to change its details or reconnect it to another model.
- With database revision `0083`, choose **Assign task** beside a saved agent. Set the task and acceptance criteria, review the exact request before starting, and find deliveries and review decisions under **Claims → Agent work**. [Assign project work](proof-workbench.md#assign-project-work)
- Use [Research actions → Research references](proof-workbench.md#work-with-the-models) to rename a claim's short code and reopen a saved proof node by its permanent number.
- Expand a **Claims** card to read its statement and Lean in place. In **Library**, the table and inspector show each entry's recorded state.
- On database revision `0079` or later, open **Chats**, choose a saved agent on the left, then select **New task** and name it. Click the current task title to switch tasks and see which ones are waiting for a reply or have failed. Each follow-up uses the agent's saved connection, and drafts stay with their tasks. Use the **Conversation history** clock beside the Chats heading to reopen earlier unassigned conversations and proof activity. Open **Agent chat** from any page, or use **Ask an Agent** from a claim or proof node's details to attach that context.
- To send, click Send or press Enter. Use Shift+Enter for a line break. Choose **Request details** from the + menu to inspect the destination and the text that will be included. **Team → Models** uses the same saved connections as **Team → Runtimes**.
- Open **Proof activity** for a selected node and choose **Save draft** to keep a private note across sessions, including while queueing is blocked. This requires database revision `0082` or later.
- On revision `0081` or later, select a node and recipient in **Proof activity**. Select **Queue proof note** or press Enter to release the displayed eligible note for a later authorized Codex proof task. The status beside the note shows whether it is waiting, blocked, tied to an older statement, or included in an attempt. [Proof guidance](proof-workbench.md#send-a-public-note-to-codex)
- Resize a graph node by dragging its lower-right corner.
- On revision `0080`, open **Edit** from either Library view. Compare the exact wording and Lean statement before confirming the new version. Old proof receipts follow their original claim, and affected checks of a replaced basis require recheck.
- On database revision `0073` or later, [review evidence](proof-workbench.md#read-a-graph) under the local researcher identity from the bound workspace.
- On database revision `0074`, new evidence approvals and returns appear automatically in an open workbench, including decisions made in the terminal.
- On database revision `0075`, the open workbench also updates after scope confirmation, withdrawal and proof task changes, including a CLI worker's progress.
- On revision `0076` with Research Multiplier `1.0.15` or later, clarifications, agent conversations and other workspace updates appear in the open workbench. Periodic refresh is used during disconnection and missed events are replayed after reconnecting.
- On revision `0077` with Research Multiplier `1.0.18` or later, Trellin's **Review** page also lets you save clarification answers, prepare the updated Lean statement, and explicitly confirm it. [Answer an interpretation](research-intake.md#answer-an-unresolved-interpretation)
- [Search Formalpedia offline](literature-search.md#search-formalpedia-locally) without creating a research database. To replay a selected result within a confirmed Lean scope, follow the same guide after enabling its database feature.
- Run `research-multiplier research resume --format text` to inspect progress and decisions in a configured project. [Resume and advancement](research-progress.md)

## Optional model suggestions

```sh
research-multiplier run inputs/question.json --model YOUR_MODEL --base-url https://api.openai.com/v1
```

Use a model available at your Chat Completions-compatible endpoint, and set `RESEARCH_MULTIPLIER_API_KEY` for a remote service. For a local service, provide its loopback URL and version path. This command sends the problem file and check result to the selected endpoint. The offline record is saved first, and the suggestions are saved in a separate `model-suggestions.md` file. This standalone command operates outside the PostgreSQL workspace's model permissions and budget. For unpublished work, use the explicit, trusted local model route in a bound workspace. [Restricted research model setup](model-adapters.md#work-with-restricted-research-in-the-terminal)
