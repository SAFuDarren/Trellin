# Contributing to Research Multiplier

## Find the code

Read the [architecture direction and design decisions](docs/architecture.md) before adding a feature. New code follows the decisions marked settled there; ask before building on an open question. Build against the [interface contracts](docs/interfaces.md), pick up work from the [roadmap](docs/roadmap.md), sign off every commit (`git commit -s`, Developer Certificate of Origin), and follow the [engineering and project management guide](docs/engineering.md), including its change checklist, for every pull request. The root README is the user entry point. Consult the [repository layout](docs/engineering.md#repository-layout) before adding files. Keep new workflow guides in [docs](docs/README.md), update both quickstarts when first-use steps change, and record release changes in [CHANGELOG.md](CHANGELOG.md). When a command's decisions, model or network use, or the main research path changes, update the [agent guide](research_multiplier/agent_guide.md) that workspaces receive as `AGENTS.md`.

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
| Packaging and compatibility checks | [Release guide](docs/release.md), [compatibility regressions](#run-the-compatibility-regressions), [GitHub Actions](.github/workflows/checks.yml) |

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

- Keep public cases and their pinned Lean projects in a separate case collection. Keep maintained regression source under `tests/`. Store downloaded fixtures, evaluation data, test reports and execution logs outside the software and real research projects. Use a disposable workspace and database for each native test lane.
- For new or revised entry points, ask the researcher to select or confirm the project folder before creating persistent research data. An explicit workspace path or an already selected, initialized project satisfies that choice. A non-interactive caller supplies its workspace in the command or configuration. Reuse that selection for subsequent work.
- Resolve the same project and database for all entry points before dispatching work. Show where persistent data will go and keep inputs, runs, and researcher-requested exports in their designated locations. See the [workspace layout](docs/research-foundation.md#workspace-paths).
- Keep existing user files in place. Preview the contents and destination of any migration or export and apply it only after the researcher chooses the destination.

Use a temporary initialized workspace for development runs. The operations guide describes setup and database binding.

<a id="run-the-external-regression-suite"></a>

## Run the compatibility regressions

Use the [maintained pytest lanes](tests/README.md) for new regression work. The
[versioned compatibility source](tests/compat_validation/PROVENANCE.md) preserves
the existing release assertions, pinning checks and CLI baseline. Use a clean
environment with the matching installed wheel and the `postgres` extra. Change
to a temporary directory and unset `PYTHONPATH`. Set `RM_TEST_DSN` in a private
environment for a disposable PostgreSQL cluster with pgvector and compatible
backup tools. Set `RM_TEST_LEAN_PROJECT` to a prepared project with Lean and
Lake 4.19.0 and the compiled Mathlib imports when running the Lean lane.

```sh
python /path/to/checkout/tests/compat_validation/run_regressions.py offline
python /path/to/checkout/tests/compat_validation/run_regressions.py postgres
python /path/to/checkout/tests/compat_validation/run_regressions.py release-lean --workers 2
```

The Lean runner clones a migrated database per case and removes its copies.
`release-lean` fails on any skip. The broader `lean` lane also accepts the
external historical flow project and two-wheel comparison inputs described
in the [original suite guide](tests/compat_validation/README.md).

## CI coverage

Pull requests and pushes to `main` run the installed-package smoke matrix and
required release regressions. A newer commit cancels the old Checks run for
the same PR. The release job checks the installed CLI baseline, the repository
PostgreSQL and Lean pytest lanes and the preserved compatibility regressions
on disposable resources. The scheduled run repeats the same
required checks. Before a release, run **Checks** on the proposed commit
with manual dispatch. For a historical candidate, supply both the trusted
HTTPS validation URL and its SHA-256 to select a different version-matched
bundle. Research examples, optional large data and run output stay external.
