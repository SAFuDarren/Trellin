# Trellin for macOS

Trellin opens the Research Multiplier workbench in a native macOS window. This local build targets macOS 15 or later on Apple Silicon. The app contains its own Python and workbench. Prepare the local PostgreSQL, pgvector and Lean installation once using the [workspace setup guide](research-foundation.md#set-up-the-workspace). Keep each workspace outside the software checkout.

## Use Trellin

1. Put `Trellin.app` in Applications or in your home folder’s Applications directory. Open Trellin from Launchpad.
2. Choose **Open Workspace…** and select the initialized workspace folder. Use **File → Open Workspace…** or **⌘O** to switch projects. Each project uses its saved connection settings. For a workspace configured only with command-line environment variables, use the existing setup flow to save its project logins first.
3. Select a graph node. Close the window or quit with **⌘Q**. Trellin reopens the last successfully opened workspace and restores its last selected graph node if it still exists. The graph, proof status and review items are read again from the project.
4. If Trellin cannot connect, check the displayed problem and try again. Use the local installation check under **Team** for Lean and database details. Follow the [workbench setup instructions](proof-workbench.md) if a database needs an upgrade.

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

For the interactive check, prepare an isolated public workspace with [local Lean and PostgreSQL](research-foundation.md). From Launchpad, open the workspace, select a noninitial graph node, close Trellin and start it again. Check the same node and the persisted verification result. Switch to a second isolated workspace and check that it shows only that project's claims. Try cancelling the folder picker and selecting an empty folder. Both should leave the selected folder untouched.
