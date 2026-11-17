# Contributing to Research Multiplier

## Find the code

Read the [architecture direction and design decisions](docs/architecture.md) before adding a feature. New code follows the decisions marked settled there; ask before building on an open question. Build against the [interface contracts](docs/interfaces.md), pick up work from the [roadmap](docs/roadmap.md), sign off every commit (`git commit -s`, Developer Certificate of Origin), and follow the [engineering and project management guide](docs/engineering.md), including its change checklist, for every pull request. The root README is the user entry point. Keep new workflow guides in [docs](docs/README.md), update both quickstarts when first-use steps change, and record release changes in [CHANGELOG.md](CHANGELOG.md). When a command's decisions, model or network use, or the main research path changes, update the [agent guide](research_multiplier/agent_guide.md) that workspaces receive as `AGENTS.md`.

| Area | Where to start |
| --- | --- |
| CLI parsing, outputs, and required roles | [`cli/`](research_multiplier/cli/) |
| Intake, research decisions, scheduling, and proof orchestration | [`workflows/`](research_multiplier/workflows/) |
| Local browser workbench | [Local transport and assets](research_multiplier/workbench/), [proof graph query](research_multiplier/workflows/proof_graph.py) |
| Lean proof, scope, and dependency checking | [`proof/`](research_multiplier/proof/), [generic Lean verifier](research_multiplier/lean_support/) |
| Workspace paths, PostgreSQL access, migrations, and snapshots | [`storage/`](research_multiplier/storage/), [`migrations/`](research_multiplier/migrations/) |
| Scope, model budgets, and schema contracts | [`policy/`](research_multiplier/policy/), [`schemas/`](research_multiplier/schemas/), [`protocol/`](research_multiplier/protocol/) |
| Pluggable models, domain checks, and literature sources | [`models/`](research_multiplier/models/), [`domain_packages/`](research_multiplier/domain_packages/), [`sources/`](research_multiplier/sources/) |
| Evaluation and paired trials | [`evaluations/`](research_multiplier/evaluations/), [evaluation guide](docs/evaluation.md) |
| Packaging and compatibility checks | [Release guide](docs/release.md), [external regressions](#run-the-external-regression-suite), [GitHub Actions](.github/workflows/checks.yml) |

The [Lean search contracts](docs/search-contracts.md) describe the local proof,
model disclosure, proposal, and evidence boundaries exercised by the regressions.
The Alembic migration tree governs the live database. Add implementation files
in the appropriate subpackage and import them there. The package root contains
the Python entry files. The `_pinned/` directories hold the exact verifier,
adapter, and historical evaluation source bytes used by recorded fingerprints.
These modules keep their established import names. The package-local
`_compat/` directory supplies the supported older entry points and dependencies
of those pinned sources. Verify these files, Lean support, schemas, the pinned
Lean setup and earlier receipts from an installed wheel when changing the layout.

The isolated Lean evaluation worker has a versioned JSON request and reply.
Its launcher loads a selected wheel and adapts its worker handler to the same
transport. A failed subprocess exit invalidates its reply.

Build model and source integrations against the documented adapter and proof
contracts. Keep provider-specific parsing and transport in the adapter. Pass
normalized candidates to the shared verifier and evidence APIs. Import code
from the subpackage responsible for that interface. Compatibility aliases and
private implementations serve existing callers and pinned historical records.
[Model adapters](docs/model-adapters.md) · [Proof and disclosure contracts](docs/search-contracts.md)

## Research data and case files

- Keep public cases and their pinned Lean projects in a separate case collection. Maintain regression source in a versioned companion bundle. Keep the bundle, generated fixtures, evaluation data, reports and execution logs outside the software and real research projects. Use a disposable workspace and database for each native test lane.
- For new or revised entry points, ask the researcher to select or confirm the project folder before creating persistent research data. An explicit workspace path or an already selected, initialized project satisfies that choice. A non-interactive caller supplies its workspace in the command or configuration. Reuse that selection for subsequent work.
- Resolve the same project and database for all entry points before dispatching work. Show where persistent data will go and keep inputs, runs, and researcher-requested exports in their designated locations. See the [workspace layout](docs/research-foundation.md#workspace-paths).
- Keep existing user files in place. Preview the contents and destination of any migration or export and apply it only after the researcher chooses the destination.

Use a temporary initialized workspace for development runs. The operations guide describes setup and database binding.

## Run the external regression suite

The regression suite for each reviewed version is stored separately from the software checkout and both Python distributions. Keep a copy of the matching validation bundle and its SHA-256 with the version's review assets. A source-only checkout can build the program and run the package smoke check.

In a clean virtual environment, install the matching review wheel, adding `[postgres]` for the database and Lean lanes. Run from outside the software checkout with `PYTHONPATH` unset.

```sh
python /path/to/validation/run_regressions.py offline
RM_TEST_DSN='postgresql://DISPOSABLE_TEST_DATABASE' \
  python /path/to/validation/run_regressions.py postgres
RM_TEST_DSN='postgresql://DISPOSABLE_TEST_DATABASE' \
RM_TEST_LEAN_PROJECT=/path/to/prepared/pinned-lean-project \
  python /path/to/validation/run_regressions.py release-lean
```

Use a disposable PostgreSQL database and a test superuser, with compatible native PostgreSQL tools and pgvector available. For Lean, put the pinned `lean` and `lake` on `PATH` and prepare the compiled Mathlib imports. The Lean lanes clone an empty migrated database per test and drop their own copies. Use `lean` for the broader native suite and `release-lean` for the required scope, dependency, recovery and privacy behaviors. The required lane fails on any skip. The wider suite also accepts the separate historical flow case and two-wheel evaluation inputs documented in the bundle.

## CI coverage

Pushes and pull requests run the installed-package smoke check and **Required release regressions**. Set `RM_VALIDATION_URL` and `RM_VALIDATION_SHA256` to the trusted HTTPS URL and SHA-256 of the version-matched external bundle. For a candidate not yet at that URL, supply both inputs to **Checks** through manual dispatch. Missing configuration, a digest or version mismatch, CLI incompatibility, or a failed required test fails the release job.

The external archive contains the runner, tests and the previous command contract baseline. CI checks existing command options and defaults, role and workspace controls, and archived verifier sources. It then runs PostgreSQL and the required native Lean lane from the installed candidate. Package smoke remains a separate check.
