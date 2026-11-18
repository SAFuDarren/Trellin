# Changes

## Unreleased

- Build and open Trellin on Apple Silicon Macs. Pick an existing research
  workspace and return to its last graph node when reopening the app.
- Trellin identifies the workspace and missing setup when a saved project
  cannot open, and offers another folder from the same window.
- Open a research folder in Trellin. On first open it sets up a `Trellin`
  subfolder with its own local PostgreSQL database.

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
