# Get started with Research Multiplier

[简体中文](quickstart.zh-CN.md) · [Documentation](README.md)

## Install

Use Python 3.10 or later. Create your Python environment outside the software checkout. On macOS or Linux:

```sh
RM_ENV="$HOME/.venvs/research-multiplier"
python3 -m venv "$RM_ENV"
. "$RM_ENV/bin/activate"
```

Install the matching wheel from the repository's Releases page. Replace the path with the file you downloaded.

```sh
python -m pip install "/path/to/research_multiplier-<version>-py3-none-any.whl"
```

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
& $RmPython -m pip install "C:\path\to\research_multiplier-<version>-py3-none-any.whl"
```

## Start with your own question

In the local proof workbench, use **Organize with AI** to review selected notes before creating a claim, or **Add theorem** to enter a theorem yourself. See [Project intake](project-intake.md).

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

For a known path, use `research-multiplier research init-workspace "PATH"` to select it explicitly. From another directory, pass `--workspace "PATH"` before the subcommand and use absolute paths for project files. [Workspace layout](research-foundation.md#workspace-paths)

## Verify and reuse a first Lean proof

From the same release as your installation, download `research_multiplier-<version>-first-proof.tar.gz` and `SHA256SUMS`. Check its SHA-256, unpack it outside the checkout, and open `first-proof/README.md`. It guides you from installation to review, verification, replay, and withdrawal with zero model calls.

For this walkthrough, use native Lean 4.19.0 and PostgreSQL 18 with pgvector on macOS or glibc Linux. In a fresh project folder and empty database, follow the walkthrough's instructions to install the PostgreSQL extra and bind the workspace. [Database setup](research-foundation.md#set-up-the-workspace) · [Native Lean setup](lean-core.md)

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
- Open [Proof task in Trellin](proof-workbench.md#start-a-proof-task) to check whether the confirmed Lean scope is ready, authorize local proof work, and monitor its result.
- Use [Research references](proof-workbench.md#work-with-the-models) to rename a claim's short code and reopen a saved proof node by its permanent number.
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
