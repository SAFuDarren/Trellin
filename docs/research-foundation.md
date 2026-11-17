# Research workflow and operations

Research Multiplier stores confirmed meanings, executable tasks and program-issued evidence in PostgreSQL. Formalizations and artifact references carry a version or a content hash. The production checkers support fixed-network maximum flow and finite graph coloring.

## Set up the workspace

The offline question workflow does not require PostgreSQL. For persistent research evidence or Lean review, select a project and configure its own PostgreSQL database. Install the `postgres` extra in your Python environment.

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

Set `RM_MIGRATION_DSN` to the authorized backup login and use PostgreSQL client tools compatible with the server. Restore a trusted archive into a new directory and a fresh database with the expected role groups.

```sh
research-multiplier research backup-project
research-multiplier research verify-project-backup ARCHIVE_DIRECTORY
research-multiplier research restore-project ARCHIVE_DIRECTORY RESTORED_DIRECTORY
```

You can schedule `backup-project` using your deployment's normal scheduler. Restore to a separate database for recovery drills. Local archives record the application and schema revisions. Maintain the release wheel, Lean and dependency archives when you need to reproduce an older environment.

For an objects-only copy, use these commands.

```sh
research-multiplier research backup-artifacts /path/to/new/artifact-backup
research-multiplier research restore-artifacts /path/to/artifact-backup
```

Artifact snapshots validate digests before restoration. Keep older runs with local-only artifacts in the backup inventory as well.
