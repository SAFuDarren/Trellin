# Research workflow and operations

Research Multiplier stores confirmed meanings, executable tasks and program-issued evidence in PostgreSQL. Formalizations and artifact references carry a version or a content hash. The production checkers support fixed-network maximum flow and finite graph coloring.

## Set up the workspace

The offline question workflow does not require PostgreSQL. For persistent research evidence or Lean review, select a project and configure its own PostgreSQL database. Install the `postgres` extra in your Python environment.

### Set up in one command

`research-multiplier setup PATH` prepares a project on an existing PostgreSQL 18 server with pgvector. Steps that are already complete are skipped, so you can run it again at any time.

1. Initializes the workspace folder, or uses the selected workspace when you omit `PATH`.
2. Installs the PostgreSQL Python packages into the current environment.
3. Connects to the server as a superuser for this run only. It tries libpq defaults (`PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, `~/.pgpass`), then the user `postgres`, then asks for a password in the terminal. Use `--host`, `--port` and `--admin-user`, or a full `RM_ADMIN_DSN`, to choose a server. The administrator password is never saved.
4. Creates the project database, its non-superuser owner, and one login for each capability group. The database accepts connections only from this project's logins, so projects that share a server cannot reach each other's data. It then installs pgvector and creates the schema.
5. Saves the five connection strings in `~/.config/research-multiplier/workspaces/WORKSPACE_ID.env` (or under `XDG_CONFIG_HOME`), readable only by you and kept outside the project so backups do not include them. Commands load this file when they select the workspace. `RM_*_DSN` variables already set in the environment take precedence, and the file is then not read.
6. Binds the workspace to the database.
7. Copies the pinned Lean project and checks for elan, Lean 4.19.0 and elan's default toolchain. Compiled Mathlib is optional; add `--mathlib` to download it (several GB).
8. Prints the [`doctor`](quickstart.md#verify-and-reuse-a-first-lean-proof) report.

When software is missing, such as a PostgreSQL server on macOS, pgvector for a Homebrew server, elan, or the Lean toolchain, setup prints the exact commands and runs them only after you confirm. `--yes` accepts these prompts for unattended runs. Without a terminal and without `--yes`, setup lists the skipped commands at the end. An existing schema is never upgraded by setup. `--skip-lean` prepares only the workspace and database.

The project logins have generated passwords. A server whose `pg_hba.conf` trusts local connections ignores those passwords, so any local user can sign in as any login. Use password authentication on servers that other people or untrusted programs can reach.

The steps below configure the same result by hand.

Start with an empty PostgreSQL database with pgvector available. Ask the database administrator to create the database and install the extension there with `CREATE EXTENSION vector;`. Use a dedicated, non-superuser database owner for migrations. The first migration needs permission to create its four capability groups, or a database administrator can create those groups ahead of time. Set `RM_MIGRATION_DSN` to the database owner's `postgresql://` URL.

```sh
python -m pip install '.[postgres]'
python -c 'import os; from research_multiplier.storage.migrate import upgrade; upgrade(os.environ["RM_MIGRATION_DSN"])'
```

For an existing workspace that already runs the features you need, keep its schema until you choose to enable a feature that requires a newer revision. Ordinary Lean search and `research resume` can still run against an initialized `0066` workspace. Back up the project before upgrading its database. [Formalpedia replay requirements](literature-search.md#search-formalpedia-locally)

After the migration, create a login per capability group. Here is an example to run as the database administrator. Configure authentication for the logins using your database's normal method. In `psql`, `\password rm_v0a_human` prompts for a password without putting it in the SQL script.

```sql
CREATE ROLE rm_v0a_human LOGIN IN ROLE rm_human;
CREATE ROLE rm_v0a_scheduler LOGIN IN ROLE rm_scheduler;
CREATE ROLE rm_v0a_agent LOGIN IN ROLE rm_agent;
CREATE ROLE rm_v0a_verifier LOGIN IN ROLE rm_verifier;
```

| Environment variable | Login in this example |
| --- | --- |
| `RM_HUMAN_DSN` | `rm_v0a_human` |
| `RM_SCHEDULER_DSN` | `rm_v0a_scheduler` |
| `RM_AGENT_DSN` | `rm_v0a_agent` |
| `RM_VERIFIER_DSN` | `rm_v0a_verifier` |

Point the four runtime connection strings at the same database. Keep database credentials in the trusted application process.

### Workspace paths

Choose a dedicated directory outside the program checkout, Python environment, and other research workspaces. Run `research init-workspace` to enter a folder in the terminal, or supply the path explicitly. If you cancel without choosing a path, initialization stops before it creates project files. The command prints the selected location. Use `research paths` to see where it will store files. This works for an offline project before it has a database.

```sh
research-multiplier research init-workspace "/path/to/my-research"
research-multiplier --workspace "/path/to/my-research" research paths
```

| Directory | Contents |
| --- | --- |
| `inputs/` | Research inputs and new question templates |
| `runs/offline/`, `runs/claims/`, `runs/lean/` | Reports and evidence from each workflow |
| `exports/` | Files selected by the researcher for sharing |
| `backups/` | User-requested snapshots |
| `objects/` | Content-addressed artifacts |
| `lean-project/` | Pinned Lean configuration and build cache when prepared |
| `evaluation/private/` | Evaluation runs and archived records |

Commands select a workspace in this order:

1. The explicit `--workspace PATH` before the subcommand.
2. `RM_DATA_ROOT` set to an initialized workspace, for unattended runs.
3. The initialized workspace containing the current working directory.

Relative file arguments resolve from your current working directory. Use absolute paths when working from elsewhere. The program validates input ownership, output categories and the selected workspace's database binding. For exports and backups, you may also choose a separate location outside the program and other workspaces. Keep public cases and validation bundles at independent paths.

Configure a separate PostgreSQL database per independent project. After configuring the role logins, bind the selected workspace.

```sh
research-multiplier --workspace "/path/to/my-research" research bind-workspace
```

The project and database identities are recorded on both sides. A different workspace or database is rejected before research work. The marker files contain identifiers. Keep database credentials with the runtime configuration.

To adopt an unbound workspace from an earlier release, run setup on its existing directory and bind it to its existing database. Upgrade its database when the feature you intend to use requires it. Choose where older files will go before moving them. Keep the artifacts with the complete run and use explicit `replay` for read-only verification of an older record.

## Confirm the basis and run the program

Use the [formalization review](v0a.md#confirm-the-statement-before-proving). The confirmed packet exposes its immutable item versions.

```sh
research-multiplier research basis PACKET_ID
research-multiplier formalization prove PACKET_ID
research-multiplier research tasks CLAIM_ID
research-multiplier research evidence CLAIM_ID
```

The default public fixed-network strategy computes a feasible flow and cut, verifies the exact certificate independently, and issues an evidence receipt without a model call. The receipt goes through researcher review. The explicit `--strategy model` flag selects the earlier model-assisted workflow for experiments. Existing staged runs continue using the strategy saved with their work.

To add a library item, give it a stable logical key. The input JSON includes `kind`, a `natural` statement, a `formal` object and its source. Confirm its exact version before admitting it to a packet.

```sh
research-multiplier research propose-item network/definition/example item.json
research-multiplier research approve-item VERSION_ID --hash VERSION_HASH --actor "your name" --reason "Meaning checked"
research-multiplier research attach-item PACKET_ID VERSION_ID --reason "Applies to this claim"
```

Opening a new version creates a separate immutable record. To withdraw an invalid version or a faulty checker, use `research retire-item` or `research retire-input checker REVISION_HASH`, with a reason. The system marks dependent evidence as `needs_recheck` and excludes it from usable results. The history remains available for inspection. A stale closed claim can be reopened for a replacement receipt with `research reopen`.

## Submit and reuse a Lean proof

For research goals confirmed in Lean, use the [general Lean checking commands](lean-core.md). The historical fixed-network proof queue uses `lean submit` and `lean work` with an explicitly selected external case project. Follow the guide in that case directory for its input format and setup.

## Recover and monitor

`research tasks CLAIM_ID` reports job IDs, states, wait and execution time, model usage where reported, recorded result reuse and pending clarifications. `research cancel JOB_ID` with actor and reason fences later submissions from that lease. Record actual review or clarification effort with `research log-time CLAIM_ID review --minutes 4`.

Use a paired project archive to back up the PostgreSQL snapshot, input files, saved runs, workspace objects, private evaluations, and Lean project sources together. The project binding identifies the database expected in the archive. The program excludes rebuildable Lake caches. It detects changes to files during backup and asks for a retry after writes settle.

Set `RM_MIGRATION_DSN` to the authorized backup login and use PostgreSQL client tools compatible with the server. For restore, set it to a `postgresql://` URL for a fresh target database with the expected role groups and choose a new destination directory. Encode spaces in a socket path as `%20` in the URL. If a later failure occurs after the database import, preserve the imported database and the archive for inspection. Retry with a fresh database and an unused destination directory.

```sh
research-multiplier research backup-project
research-multiplier research latest-backup
research-multiplier research verify-project-backup ARCHIVE_DIRECTORY
research-multiplier research restore-project ARCHIVE_DIRECTORY RESTORED_DIRECTORY
```

`latest-backup` looks in this project's `backups/`, or in a directory you choose with `--directory`. It tries the newest manifests by modification time and returns the first archive whose workspace binding and file hashes match. Run a restore drill when you need to confirm recovery in another environment. The lookup works without a running PostgreSQL server.

You can schedule `backup-project` using your deployment's normal scheduler. Restore to a separate database for recovery drills. Local archives record the application and schema revisions. Maintain the release wheel, Lean and dependency archives when you need to reproduce an older environment.

For an objects-only copy, use these commands.

```sh
research-multiplier research backup-artifacts /path/to/new/artifact-backup
research-multiplier research restore-artifacts /path/to/artifact-backup
```

Artifact snapshots validate digests before restoration. Keep older runs with local-only artifacts in the backup inventory as well.

## Take a research handoff with you

Preview a claim with `research handoff --claim CLAIM_UUID`. Repeat `--claim` to include more claims, or use `--project` for current claims and the project's open decisions and drafts. The preview includes the exact research text and records that will appear in the report. Review them before saving a copy for someone else.

```sh
research-multiplier research handoff --claim CLAIM_UUID
research-multiplier research handoff --claim CLAIM_UUID \
  --out exports/first-handoff --confirm SNAPSHOT_SHA256
```

Replace `SNAPSHOT_SHA256` with the digest from the preview. If a selected record changes, preview again. Choose a new output directory under the project's `exports/` or under an existing parent folder elsewhere. Use `--language zh-CN` on both commands for Chinese headings. Research text stays as entered.

The directory contains one `handoff.html` that you can open and print offline, and one editable `handoff.md`. Both show the same snapshot date and digest. They include the statement, approved assumptions, actual and planned proof dependencies, verification and review status, source links, recorded notes, and next steps. App credentials and model transcripts are outside this export. To restore a working project, keep the verified machine archive as well.
