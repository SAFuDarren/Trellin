# Prepare a release

English · [简体中文](release.zh-CN.md)

A release includes the Python package, the generic Lean verifier and Mathlib configuration, migrations, and schema contracts. Keep case materials and evaluation datasets in separate directories. Research inputs, saved runs, credentials and database snapshots belong to each project workspace.

## Verify the distribution

Build the wheel and source package in a separate working area. Install the wheel with the PostgreSQL extra in a clean environment and run the [repository and compatibility regressions](../CONTRIBUTING.md#run-the-compatibility-regressions) with a disposable database and the pinned Lean environment. From outside the checkout, verify that the installed command can prepare the generic Lean environment. Run database upgrade checks from the previous supported revisions and replay a previous immutable run. Run both the PostgreSQL suite and the required `release-lean` cases against the installed candidate. Record the broader `lean` lane separately with its optional inputs and any omitted tests.

## Verify first use

Use the matching external `first-proof` case. Follow its README from a fresh virtual environment, empty PostgreSQL database and distinct project directory. Review the exact premise and theorem, verify and adopt the proof, reuse it under a second claim's approved basis, and verify that withdrawal invalidates the dependent evidence. The case uses no model service. Keep the case inputs and run records outside the software distribution.

Attach the matching wheel, source distribution, `research_multiplier-<version>-first-proof.tar.gz`, and `SHA256SUMS` to the same release tag. The first-proof archive starts with `first-proof/README.md`. CI uses the matching compatibility source in the repository. A historical candidate may use its separately verified validation archive. Verify the asset names and SHA-256 values against the quickstart before publishing.

Check that the wheel and source distribution include the project [Apache License 2.0](../LICENSE) and [NOTICE](../NOTICE) and declare `Apache-2.0` in their package metadata. Include the first-proof archive's license and notice before generating `SHA256SUMS`. Preserve the compatibility suite's original MIT license when archiving its unchanged sources.

## Rehearse release recovery

Run this drill before the first public distribution and before an incompatible upgrade. Use a disposable project and a PostgreSQL database for each side of the restore. Install the previous and candidate wheels with the PostgreSQL extra in separate virtual environments. Record each wheel's version, source commit and verified SHA-256. A checksum-verified internal build can serve as the previous version before the first public release. Run the research subcommands with the relevant environment's `research-multiplier` CLI, selecting the project directory for workspace commands.

1. With the previous version, check and adopt the public first-proof case and save an offline run. Record the database revision, frozen claim, verification receipt and run path. Use the authorized source database URL to run `research backup-project --out ARCHIVE`, then run `research verify-project-backup ARCHIVE`.
2. With the candidate environment, run the [documented migration command](research-foundation.md#set-up-the-workspace) on that same disposable database. Record the resulting revision and check that the candidate reads the frozen claim and the same receipt, replays the old run, and saves a new run.
3. Switch back to the previous environment. Set `RM_MIGRATION_DSN` to the new empty database and run `research restore-project ARCHIVE RESTORED_DIRECTORY`. Configure logins for that database. Verify the new workspace and database binding, compare the restored frozen claim and receipt, and replay the archived run with the previous version. Check the archive digests and compare restored files through the manifest, allowing the workspace and database markers to differ. Check that work created after the backup remains in the upgraded workspace.
4. Keep the latest applicable rehearsal result with each release review outside the checkout. Record the artifact hashes, source commits, schema revisions, comparison results and recovery commands. See the [archive guide](research-foundation.md#recover-and-monitor) for backup and restore options.

### Internal rehearsal for issue #17

The archived 1.0.1 wheel built from `2daacac` (the same source tree as `893582e`) and the 1.0.3 wheel built from `cb9a3cd` were run in separate environments with local PostgreSQL and Lean 4.19.0. The candidate read the original frozen claim and proof receipt, replayed the previous run, and saved a new one. The previous wheel restored the pre-upgrade archive into a fresh database, verified its digests, read the same claim and receipt, and replayed the archived run. All 16 archived project files matched byte for byte, with new workspace and database identity markers. The candidate run was absent from the restored workspace. Both wheels used database revision `0071`. This rehearsal covers rollback across program versions at the same schema revision.

## Record the release

Record the distribution version, supported Python and Lean versions, predecessor and current database revisions, and distribution hashes. Keep the versioned regression archive and its SHA-256 alongside the review record. Preserve the external logs for the lanes that ran, including skipped optional inputs. Confirm repository ownership, visibility and the project license before publishing. Keep deployment credentials in runtime configuration.

Read the source version from [pyproject.toml](../pyproject.toml) and the schema head from the packaged Alembic migrations. See the [workspace backup and migration guide](research-foundation.md#set-up-the-workspace) before upgrading an active project. Check the supported features against the [research workflows](README.md#research-workflows).
