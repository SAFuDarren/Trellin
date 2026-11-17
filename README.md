# Research Multiplier

**A local workspace for AI-assisted theoretical research.**

Explore more proof routes and spend more time on the questions you want to pursue. You choose the research question and approve its formal meaning and assumptions. Research Multiplier uses deterministic checks, Lean, and optional models to verify proofs, reuse established results, and collect decisions in `research resume`.

[English quickstart](docs/quickstart.md) · [简体中文快速入门](docs/quickstart.zh-CN.md) · [Full documentation](docs/README.md)

## Start here

| If you want to… | Start with… |
| --- | --- |
| Try it with your own question | [Offline quickstart](docs/quickstart.md#start-with-your-own-question). Python 3.10 or later is enough to save a research brief and replay supported checks. |
| Verify your first Lean proof | [First-proof walkthrough](docs/quickstart.md#verify-and-reuse-a-first-lean-proof). Confirm a theorem and its assumptions, verify it, reuse it for a second claim, and withdraw it. Uses Lean 4.19.0 and PostgreSQL with pgvector on macOS or glibc Linux. |
| Bring an existing proof | [Check a `.lean` file or Lake declaration](docs/lean-core.md#check-a-lean-source-file-or-compiled-declaration) and inspect the assumptions and theorems actually used. |

For an initialized PostgreSQL research project, open the [local proof workbench](docs/proof-workbench.md) to inspect exact Lean statements, dependencies, open proof obligations, and the impact of withdrawing an assumption.

## Where files go

The software checkout contains the program, generic Lean support, packaging, and documentation. Choose a separate folder for each research project. Run `research-multiplier research init-workspace` to enter your path, or pass the path explicitly. `research-multiplier --workspace PATH research paths` shows where that project's files go.

Each project keeps its inputs, run reports, proof objects, exports, and backups in its selected folder. Persistent Lean workflows bind it to its own PostgreSQL database. Keep public cases and regression bundles in separate folders. [Workspace layout and backup](docs/research-foundation.md#workspace-paths)

## What the system can do

- Freeze an exact claim and its approved basis, check its proofs or refutations, and show which evidence is still valid.
- Check and save derived lemmas, replay valid proofs within a workspace, and mark affected results for recheck when their basis is withdrawn.
- Search a local Formalpedia snapshot, advance supported proof routes under a budget, and show evidence, open obligations, and decisions in `research resume`.

The current version supports bounded Lean search and deterministic checks for selected domains. Core verification and supported replay can run with zero model calls. [Research workflows](docs/README.md#research-workflows)

[Contributor guide](CONTRIBUTING.md) · [Version history](CHANGELOG.md)

## License

The program and project documentation are licensed under the [Apache License 2.0](LICENSE); see [NOTICE](NOTICE). Version 1.0.0 and earlier releases remain available under the MIT License. Your research data, proofs and results are yours and are not covered by this license. The project name and logo follow the [name and logo policy](TRADEMARKS.md).
