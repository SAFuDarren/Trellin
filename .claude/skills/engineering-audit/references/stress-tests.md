# Six stress tests

Checklists show that rules exist; these tests show that they work. Record each result as passed, failed or not run, with evidence. Never record a test that did not run as passed.

| Test | How to run it here | Passes when |
| --- | --- | --- |
| 1. Cold start | Clone into a temporary directory, create a fresh virtual environment, install the package (`pip install ".[postgres]"`), follow the offline quickstart (`init-workspace`, `init`, `run`, `replay`). If a disposable PostgreSQL is available, also run `setup` and the first-proof walkthrough. | Everything works from the written steps alone, with no hidden configuration from the developer's machine. |
| 2. New feature | Take the next roadmap task. Before coding, list the modules expected to change. After the PR, compare with the actual `git diff --stat`. | The task is implemented, tested and integrated, and the real change touches about the predicted modules, with no unrelated rework. |
| 3. Parallel development | Ask the user first. Two agents or developers take two independent tasks in separate worktrees, then integrate both. | Both land against stable interfaces without overwriting each other or needing a redesign. |
| 4. Regression | In a temporary worktree, inject known faults one at a time, run `python -m pytest -m offline` after each, and revert. Never commit. Examples: drop the session-token check in `workbench/server.py`; loosen the `Z` timestamp pattern in `event.schema.json`; make `agent_guide.install` overwrite an edited guide. | Every injected fault makes at least one automated test fail. Any that pass silently are gaps to record. |
| 5. Recovery (research version) | Replay a recorded run with `research-multiplier replay`. Back up a disposable project and restore it into a new empty database, then compare. | The recorded result reproduces exactly from the code version, parameters, dependencies and data, and the restored project matches the original. |
| 6. Handover | Ask the user first. Give a fresh agent, with no conversation history, an ordinary task (for example: add a field to one workbench endpoint, with a test and docs) and only the repository. | The agent finishes using only `AGENTS.md` and the docs, and follows the rules without being told: A2, signed-off commits, tests, both doc languages, no edits under `_pinned/`. |

Tests 1, 4 and 5 are cheap; run them in every audit when the environment allows. Tests 2, 3 and 6 are best run alongside real roadmap work.
