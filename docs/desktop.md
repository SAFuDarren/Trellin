# Trellin for macOS

Trellin opens the Research Multiplier workbench in a native macOS window. This local build targets macOS 15 or later on Apple Silicon. The app contains its own Python and workbench. Prepare the local PostgreSQL, pgvector and Lean installation once using the [workspace setup guide](research-foundation.md#set-up-the-workspace). Keep each workspace outside the software checkout.

## Use Trellin

1. Put `Trellin.app` in Applications or in your home folder’s Applications directory. Open Trellin from Launchpad.
2. Choose **Open Folder…** and select your research folder. On first open Trellin creates a `Trellin` subfolder and its own database on the local PostgreSQL 18 server with pgvector, then opens it. Opening the same folder again reuses its workspace. You can also open an initialized Trellin workspace directly.
3. Use **File → Open Folder…** or **⌘O** to switch folders. Each workspace uses its saved connection settings. For a workspace configured only with command-line environment variables, use the existing setup flow to save its project logins first.
4. Select a graph node. Close the window or quit with **⌘Q**. Trellin reopens the last successfully opened workspace and restores its last selected graph node if it still exists. The graph, proof status and review items are read again from the project.
5. If opening or restoring a workspace fails, Trellin shows the folder and the reason. An item already using the `Trellin` name stays where it is. Choose **Open Folder…** to select another location. If the server is temporarily down, use **Try Again** when it is ready. Use the local installation check under **Team** for Lean and database details, and follow the [workbench setup instructions](proof-workbench.md) if a database needs an upgrade.

First open uses the local Unix socket at `/tmp` on port `5432` by default, with the macOS user or `postgres` as the setup administrator. Launchers for another local installation can provide `RM_DESKTOP_PG_SOCKET` and `RM_DESKTOP_PG_PORT`. Project logins are saved through the existing setup workflow.

The app keeps the last successful folder and graph selection in macOS preferences. The workspace keeps its own research files and proof records. The loopback service starts on a free port with a fresh session for each workspace open. Project review and external model authorization use the same rules as the browser workbench. The app leaves schema migration and dependency installation to the normal tools.

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

Use the signed app for startup and workspace checks. For the interactive check, use a disposable directory with [local Lean and PostgreSQL](research-foundation.md). From Launchpad, cancel the picker once and check that the directory has no `Trellin` subfolder. Then open it, verify that the subfolder and database are created, and select the same directory again to check that it opens the existing workspace. In an isolated public example with an existing graph, select a noninitial node and restart Trellin to check that it restores the same node and persisted verification result.

After quitting the app, verify the same bundle’s signature.

```sh
codesign --verify --deep --strict /absolute/path/to/local-review/Trellin.app
```
