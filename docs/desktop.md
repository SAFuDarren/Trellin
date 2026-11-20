# Trellin for macOS

Trellin opens the Research Multiplier workbench in a native macOS window. This local build targets macOS 15 or later on Apple Silicon. The app contains its own Python and workbench. Prepare the local PostgreSQL, pgvector and Lean installation once using the [workspace setup guide](research-foundation.md#set-up-the-workspace). Keep each workspace outside the software checkout.

## Use Trellin

1. Put `Trellin.app` in Applications or in your home folder’s Applications directory. Open Trellin from Launchpad. With no previously opened project, Trellin creates and opens the default **Library** under `~/Library/Application Support/Trellin/Library`.
2. Use **File → New Project…** to create an independent project in the library. Use **File → Open Project…** to select a registered project. Each project has its own workspace ID, files and PostgreSQL binding.
3. Use **File → Open Folder…** or **⌘O** to open a project at a chosen location. You can select an initialized workspace directly. For an ordinary research folder, Trellin creates or opens its dedicated `Trellin` subfolder. Original research files stay where you keep them. For a project configured only through environment variables, use the existing setup flow to save its database logins.
4. Select a graph node and quit with **⌘Q**. On the next open, Trellin restores the last successfully opened project and, when it still exists, the selected node. Existing projects reopen at their registered paths.
5. If the database service is temporarily unavailable, restart that service and use **Try Again**. The app uses the local PostgreSQL 18 server with pgvector. Defaults are the Unix socket at `/tmp` and port `5432`. For another local setup, set `RM_DESKTOP_PG_SOCKET` and `RM_DESKTOP_PG_PORT`. The [workbench guide](proof-workbench.md) covers database upgrades.

Add installed models from [Team → Runtimes](project-intake.md#connect-a-model). For a managed installation with tools outside the standard locations, the Trellin launch environment can supply colon-separated absolute directories through `RM_DESKTOP_TOOL_PATH`.

If **Proof activity** needs a newer database, choose **Back up and upgrade**. Confirm the project path in the Mac dialog. Trellin verifies a local archive containing the project files and PostgreSQL snapshot before it upgrades the selected database, then reopens the project. Keep the archive for recovery. The dialog requires this project’s saved database owner login.

## Manage storage

Open **Trellin → Settings… → Storage** or press **⌘,** to see the active library location and open it in Finder. **Move Data…** lets you select the parent for a new **Trellin Library** folder. Review the exact old and new paths and confirm the move. Trellin copies the managed library, verifies its files, workspace IDs and existing database bindings, then switches the registered location. The original is retained as a recovery copy. An interrupted or failed check leaves the original library active. Open a managed project's new location from **Open Project…**.

The move applies to the library and the managed project files under it. External projects and explicitly exported files stay at their chosen locations. PostgreSQL keeps its existing storage and database bindings. Trellin's application support directory retains the small location record and application settings. macOS preferences remember the last project, and database logins remain in their protected configuration.

Use the existing [project backup workflow](research-foundation.md#recover-and-monitor) to preserve a project's files and PostgreSQL snapshot together. Keep app preferences and the registered library location separately when backing up Trellin's settings. A project archive can be restored as an independent project.

**Settings → Software updates** shows the installed version and a manual check for new stable releases. Automatic checks are off by default. If enabled, Trellin checks at most once every 24 hours while the workbench is open. The check requests public release metadata from GitHub without sending project data or credentials. You can open the release notes in your browser and choose when to install the update.

## Build for local review

Use an Apple Silicon Mac with the Xcode Command Line Tools, Git and HTTPS access to the runtime and package archives. Build from a Git checkout with the compiled workbench committed. The build checks the runtime archive and wheel hashes. The output folder must already exist outside the checkout.

```sh
desktop/macos/build.sh /absolute/path/to/local-review
```

The result is an ad hoc signed `Trellin.app` for local review. To run the automated desktop checks and existing offline tests from the checkout:

```sh
python -m pytest -m offline
desktop/macos/tests/run-swift-tests.sh
```

## Validate the signed build

Use a fresh virtual environment outside the checkout and the app for the [installed-wheel compatibility suite](../tests/compat_validation/README.md). Install the wheel with the exact version declared in `tests/compat_validation/PACKAGE_VERSION` and keep test projects, databases and logs in disposable locations. The runner rejects a Python interpreter or package installed inside a macOS app before it starts the tests.

Use the signed app from Launchpad with an isolated application profile, a disposable data home, and [local Lean and PostgreSQL](research-foundation.md). Verify that a clean first launch creates and opens the default library, and that restarting restores the same workspace. Use a public managed case and a separate public external case to verify both database bindings and saved proof histories. In Storage settings, preview and confirm the move to an unused location. Reopen both projects and compare their IDs, evidence, and files with the original records. Verify that a rejected destination leaves the active library usable.

After quitting the app, verify the same bundle’s signature.

```sh
codesign --verify --deep --strict /absolute/path/to/local-review/Trellin.app
```
