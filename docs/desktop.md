# Trellin for macOS

Trellin opens the Research Multiplier workbench in a native macOS window. This local build targets macOS 15 or later on Apple Silicon. The app contains its own Python and workbench. Prepare the local PostgreSQL, pgvector and Lean installation once using the [workspace setup guide](research-foundation.md#set-up-the-workspace). Keep each workspace outside the software checkout.

## Use Trellin

1. Put `Trellin.app` in Applications or in your home folder’s Applications directory. Open Trellin from Launchpad. With no previously opened project, Trellin creates and opens the default **Library** under `~/Library/Application Support/Trellin/Library`.
2. Use **File → New Project…** to create an independent project in the library. Use **File → Open Project…** to select a registered project. Each project has its own workspace ID, files and PostgreSQL binding.
3. Use **File → Open Folder…** or **⌘O** to open a project at a chosen location. You can select an initialized workspace directly. For an ordinary research folder, Trellin creates or opens its dedicated `Trellin` subfolder. Original research files stay where you keep them. For a project configured only through environment variables, use the existing setup flow to save its database logins.
4. Select a graph node and quit with **⌘Q**. On the next open, Trellin restores the last successfully opened project and, when it still exists, the selected node. Existing projects reopen at their registered paths.
5. If the database service is temporarily unavailable, restart that service and use **Try Again**. The app uses the local PostgreSQL 18 server with pgvector. Defaults are the Unix socket at `/tmp` and port `5432`. For another local setup, set `RM_DESKTOP_PG_SOCKET` and `RM_DESKTOP_PG_PORT`. The [workbench guide](proof-workbench.md) covers database upgrades.

Press **⌘K** (**Ctrl+K** on other platforms) or use the search button in the left rail to open the **Command palette** anywhere in the workbench. Search the current project for a claim or proof node by title, a complete reference such as `FLOW-09`, a Library entry, a conversation or an Agent. Proof nodes in the open claim appear first, followed by matches from the 40 most recent claims on the Global map. A complete saved reference can open an older node directly. The palette can also open **Add theorem**, **Organize with AI**, **Research references**, the selected claim’s **Proof task**, or a new chat draft. Review and confirm work in the panel that opens.

The project map, Claims board and inspector show recorded proof obligation counts. When none are recorded, the progress label explains what will appear there. In Claims or the claim inspector, open **Results to review** to inspect the recorded results for that claim.

Use **Find claim** on the project map to search and select a card. In **Agent chat**, search for a recipient or conversation from its chooser. In these search controls and the command palette, use arrow keys to move through options, Enter to select, and Escape to return focus.

In the command palette, start with `#` to search claims, proof nodes and saved references, or `@` to search Agents. The footer lists these prefixes and navigation keys. Press the displayed shortcut, for example **⌥P** on Mac or **Alt+P** elsewhere, while the menu or palette is open.

The **Details** and **Global map** icons show when their panels are open. With focus on the project map or its toolbar, press **I** for Details or **M** for Global map. Point at or keyboard-focus an icon to see its shortcut.

Open a proof to start at a readable zoom on the main canvas. **Fit to window** shows the whole graph. Select a node to read its full formula and Lean statement in the inspector. Hover over an activity count or check the inspector to see the recorded Agent and model. **Edge styles** opens the legend.

Right-click a project map card or proof node for its actions. With the item focused, press **Shift+F10** to open the same menu. **Write proof note** opens the note editor for that node. **Show in Library** lists recorded sources used by the claim.

Open **Agent chat** to start typing, or press **@** at the start of a draft to choose a recipient. If the project has one saved Agent, the composer selects it and displays its name. With multiple Agents, choose a recipient and the draft remembers it. Use **Add an Agent** if the project has none. The suggested prompts fill the draft. **Request details** opens the local conversation and shows the outgoing message for review. Send with **Enter** or **Send**. Use **Ask an Agent** on a claim or proof node when you want to attach that context. **Open full conversation** moves the current conversation and its draft to Chats, where the dock remains collapsed.

Use **Agent activity** in the bottom bar to see recorded proof and model jobs, Agent work, conversations and results waiting for you. Filter by state and open an item to go to its job, proof, conversation or review. The Team badge counts running activity across the project. Claims and Chats show the count for the claim or proof node they display.

In Library, hover over a row or focus it with the keyboard to reveal its **Edit statement** icon. From a focused row, press **Tab** and then **Enter** to open the revision form. You can also open it from the entry inspector.

The project map, claim inspector and Claims page show the claim’s recorded review, decision and verification state. Select a claim to load its current proof task. The count covers obligations recorded in reduction routes for the current Lean statement. An obligation counts as verified while its checked proof remains current. If two routes record the same proposition, each occurrence counts in its route.

Team opens on **Agents**. Add installed models from [Team → Runtimes](project-intake.md#connect-a-model), where **Environment check** has its own section. For a managed installation with tools outside the standard locations, the Trellin launch environment can supply colon-separated absolute directories through `RM_DESKTOP_TOOL_PATH`.

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
