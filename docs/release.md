# Prepare a release

A release includes the Python package, the generic Lean verifier and Mathlib configuration, migrations, and schema contracts. Keep case materials and evaluation datasets in separate directories. Research inputs, saved runs, credentials and database snapshots belong to each project workspace.

## Verify the distribution

Build the wheel and source package in a separate working area. Install the wheel with the PostgreSQL extra in a clean environment and run the [matching external regressions](../CONTRIBUTING.md#run-the-external-regression-suite) with a disposable database and the pinned Lean environment. From outside the checkout, verify that the installed command can prepare the generic Lean environment. Run database upgrade checks from the previous supported revisions and replay a previous immutable run. Run both the PostgreSQL suite and the required `release-lean` cases against the installed candidate. Record the broader `lean` lane separately with its optional inputs and any omitted tests.

## Verify first use

Use the matching external `first-proof` case. Follow its README from a fresh virtual environment, empty PostgreSQL database and distinct project directory. Review the exact premise and theorem, verify and adopt the proof, reuse it under a second claim's approved basis, and verify that withdrawal invalidates the dependent evidence. The case uses no model service. Keep the case inputs and run records outside the software distribution.

Attach the matching wheel, source distribution, `research_multiplier-<version>-first-proof.tar.gz`, and `SHA256SUMS` to the same release tag. The first-proof archive starts with `first-proof/README.md`. The versioned validation bundle is a separate artifact used by CI. Verify the asset names and SHA-256 values against the quickstart before publishing.

Check that the wheel and source distribution include the project [Apache License 2.0](../LICENSE) and [NOTICE](../NOTICE) and declare `Apache-2.0` in their package metadata. Include the same license and notice in the standalone validation and first-proof archives before generating `SHA256SUMS`.

## Record the release

Record the distribution version, supported Python and Lean versions, predecessor and current database revisions, and distribution hashes. Keep the versioned regression archive and its SHA-256 alongside the review record. Preserve the external logs for the lanes that ran, including skipped optional inputs. Confirm repository ownership, visibility and the project license before publishing. Keep deployment credentials in runtime configuration.

Read the source version from [pyproject.toml](../pyproject.toml) and the schema head from the packaged Alembic migrations. See the [workspace backup and migration guide](research-foundation.md#set-up-the-workspace) before upgrading an active project. Check the supported features against the [research workflows](README.md#research-workflows).
