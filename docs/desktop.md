# Trellin for macOS

Trellin opens the Research Multiplier workbench in a native macOS window. This local build targets macOS 15 or later on Apple Silicon. The app contains its own Python and workbench. Prepare the local PostgreSQL, pgvector and Lean installation once using the [workspace setup guide](research-foundation.md#set-up-the-workspace). Keep each workspace outside the software checkout.

## Use Trellin

1. Put `Trellin.app` in Applications or in your home folder’s Applications directory. Open Trellin from Launchpad. With no previously opened project, Trellin creates and opens a project named **My research** in its library under `~/Library/Application Support/Trellin/Library`.
2. Use **File → New Project…** to create an independent project in the library. Use **File → Open Project…** to select a registered project. Each project has its own workspace ID, files and PostgreSQL binding.
3. Use **File → Open Folder…** or **⌘O** to open a project at a chosen location. For an ordinary research folder, Trellin saves the workspace in its library and links the chosen folder there. Selecting the folder again reopens the same project. You can select an initialized workspace directly or reopen one in an existing `Trellin` subfolder. Original research files stay where you keep them. For a project configured only through environment variables, use the existing setup flow to save its database logins.
4. Select a graph node and quit with **⌘Q**. On the next open, Trellin restores the last successfully opened project and, when it still exists, the selected node. Existing projects reopen at their registered paths. Trellin keeps the library ready itself: if the last project can no longer be reopened, for example because it belongs to another Trellin installation or its folder was removed, Trellin opens your library instead and says so in a short notice with **Open Project…**.
5. If the database service is temporarily unavailable, restart that service and use **Try Again**. The app uses the local PostgreSQL 18 server with pgvector. Defaults are the Unix socket at `/tmp` and port `5432`. For another local setup, set `RM_DESKTOP_PG_SOCKET` and `RM_DESKTOP_PG_PORT`. The [workbench guide](proof-workbench.md) covers database upgrades.

The left rail has five spaces: **Home**, **Work**, **Chats**, **Team** and **Automation**. **Home** opens the **Project map** directly beside the rail, with connection status next to the zoom controls in the map footer. **Work** opens on the claims board. Its panel starts with **Needs you**: results to approve, questions to answer, failed checks and Agent deliveries, newest first, each in one row whose **Handle** and **Open page** actions appear when you point at it. **Needs you** is the one count of what waits: the rail badge counts the same items, and chips under its heading narrow it to one kind, such as results to approve or starting scopes to confirm. **Views** count all claims, running work, blocked claims and claims **Ready to advance**: open claims whose prerequisites are all proved; **Pinned** holds the claims you pin; **Records** opens **Decision records**, **Agent tasks** and **Library**. Each panel section folds from its heading, and a record page's own groups, such as Library's kinds and sources, hang under its entry and fold with it. **Handle**, **Go through** or a claim card opens a window floating over the board, below the page header; the board keeps its layout, and the claim the item belongs to is marked, and the board does not move; scroll it by hand to bring a card out from under the window. Drag the window's left edge, or focus it and use the arrow keys, to resize it between 420 and 900 pixels; a double-click restores the default, and the width is remembered: record a decision there, and the next item takes its place; **J** and **K** move through the list and **Esc** closes it. A starting scope to confirm opens with its statement and each outstanding item: confirm or omit each one, add a Lean proposition where it has none, and give one reason. On the **Decision records** page, **Handle** opens the item in the page rather than beside it. A kind of review item that Trellin cannot record yet says so rather than pointing to the command line. A card shows how a claim was established: **Lean-checked**, **Certificate-checked** or **Audited proof**, with a check when Review approved that evidence, and **Numeric support** on an open claim that a numeric check supports but has not proved. It also lists the claims it uses and how many claims use it; point at or select a card to highlight both and dim the rest, and the side panel lists them under **Relies on** and **Used by**, each opening in place. Switch or create projects from the project tile at the top of the rail. Point at a project and choose **⋯** to rename it or remove it from the list. Removing never deletes the project: its folder and database stay on this Mac, and **Open Folder…** adds it back. The project you have open and **My research** cannot be removed. Once you have a project of your own, an empty **My research** leaves the list; Trellin still opens it if nothing else can open. Opening a project folder and changing the library location are in **Settings → Advanced**. **Automation** starts from **New automation** and two templates, and nothing runs until you approve the exact rule. [Automation](research-progress.md)

Press **⌘K** (**Ctrl+K** on other platforms) or use the search button in the left rail to open the **Command palette** anywhere in the workbench. Search the current project for a claim or proof node by title, a complete reference such as `FLOW-09`, a Library entry, a conversation or an Agent. Proof nodes in the open claim appear first, followed by matches from the 40 most recent claims on the Global map. A complete saved reference can open an older node directly. The palette can also open **New theorem…**, **Paper, notes or code…**, **Research references**, the selected claim’s **Proof task**, or a new chat draft. Review and confirm work in the panel that opens.

The project map, Claims board and inspector show recorded proof obligation counts. When none are recorded, the progress label explains what will appear there. In Claims or the claim inspector, open **Results to review** to inspect the recorded results for that claim.

The **+** button in the project map header (**Add to project**) brings material in: **Paper, notes or code…** opens the import, and **New theorem…** enters one theorem. Drop a text, Markdown, TeX or Lean file anywhere on the map to import it; a project without claims shows a drop area in the middle of the map. The map's tools are icons that show their names on hover and focus. Work on one claim, such as **Local lemmas**, **Premises** and **Research references**, is in the claim's context menu.

Use **Find claim** on the project map to search and select a card. In **Agent chat**, search for a recipient or conversation from its chooser. In these search controls and the command palette, use arrow keys to move through options, Enter to select, and Escape to return focus.

In the command palette, start with `#` to search claims, proof nodes and saved references, or `@` to search Agents. The footer lists these prefixes and navigation keys. Press the displayed shortcut, for example **⌥P** on Mac or **Alt+P** elsewhere, while the menu or palette is open.

Open **Help** at the bottom of the rail for short guides to the project map, Agent tasks and proof review. Each guide links to the relevant workbench page. Press **?** or open **Keyboard shortcuts** from Help to see the shortcuts for your platform. The **Details** and **Global map** icons show when their panels are open. With focus on the project map or its toolbar, press **I** for Details or **M** for Global map. Point at or keyboard-focus an icon to see its shortcut.

The proof header shows the path from the project map to the claim and selected node. Choose the claim name to select its goal, or the map control to return to the canvas. In Agent tasks, a handoff task links to its source delivery in the task path.

The open claim and node are part of the address, as `#workbench/claim/<claim-id>/node/<node-id>`. Use the browser's Back and Forward to move between the map, the claim and the claim you opened before, or copy the address to come back to the same proof. Choosing another node in the same proof updates the address without adding a Back step. If the claim or node no longer exists, the map or the claim's goal opens with a notice.

Arrow keys pan when the canvas has focus. To move a focused card, open its context menu with **⇧F10** (**Shift+F10** on other platforms) and choose **Move card**. Use the arrow keys to preview the new position. Hold **Shift** for larger steps. Press **Enter** to save the position or **Escape** to cancel.

Open a proof to start at a readable zoom on the main canvas. **Fit to window** shows the whole graph. Select a node to read its full formula and Lean statement in the inspector. Hover over an activity count or check the inspector to see the recorded Agent and model. The footer shows labelled proof counts. In a narrow window, **Status** opens the full list when the labels run out of space. **Edge styles** opens the legend for the current view. Switch to **References** to see recorded uses, allowed premises and planned steps distinguished by line style.

Changing the interface language keeps proof nodes in the same order and preserves their references and saved card sizes.

In **If withdrawn**, the graph legend and inspector show the same total of affected records, including other claims, and identify the premise being previewed. Open other proof actions from **Proof**.

In a narrow inspector, each affected record has a full-width title with its status on the next line. The impact summary sits below the list heading.

Selected map cards, proof nodes and review rows have a Navy edge and a light tint. Hover adds a neutral highlight, and keyboard focus adds an offset outline. A spinner and count inside a proof node show its running jobs.

Drag from a card’s bottom handle to connect two claims. The preview shows the saved route and both attachment points over an eligible card. Select a link to see the two claim names, remove it or undo a removal.

Click a project map card to inspect the claim. Double-click the card or press **Enter** on it to open its proof. Use **Project map** or **Escape** to return. In a narrow window, **Escape** first closes the details drawer and leaves the selected node active. The zoom buttons sit below the canvas. Right-click a card or proof node for its actions, or press **Shift+F10** with the item focused. **Write proof note** opens the note editor for that node. **Show in Library** lists recorded sources used by the claim.

Open **Agent chat** to start typing, or press **@** at the start of a draft to choose a recipient. A new conversation starts with the Agent you talked to most recently, or the first saved Agent when there is no conversation yet, and displays its name. Choose another recipient from the chooser and the draft remembers it. Use **Add an Agent** if the project has none. The suggested prompts fill the draft. **Request details** opens the local conversation and shows the outgoing message for review. Send with **Enter** or **Send**. Use **Ask an Agent** on a claim or proof node when you want to attach that context. **Open full conversation** moves the current conversation and its draft to Chats, where the dock remains collapsed.

Agent chat opens at the lower right, clear of the inspector and canvas controls. Drag the top or left edge, or the top-left corner, to resize. Focus an edge and use the arrow keys to resize with the keyboard. Use **Maximize Agent chat** to fill the available area and **Restore chat size** to return to your saved size. Collapsing the panel, switching pages or reopening Trellin keeps your size. Suggestions follow the selected claim or proof node. An existing conversation has a **Send without earlier messages** checkbox that keeps previous turns out of each request while it is checked.

Use **Agent activity** in the bottom bar to see recorded proof and model jobs, Agent tasks, conversations and results waiting for you. Filter by state and open an item to go to its job, proof, conversation or review. The Team badge counts running activity across the project. Claims and Chats show the count for the claim or proof node they display. Choose **Open full activity** in the drawer for the same list in a full page. **Team → Jobs** stays separate: it is a table of each proof and model job with its model, provider and run time. The **Work** rail icon shows how many items are waiting for you. A chat without messages opens at the height of its composer and suggestions.

**Work → Decision records** lists the decisions you and others recorded, newest first: results approved or returned, starting scopes confirmed, and claims frozen, withdrawn or restored, each with who decided, the reason and when. Filter it to results, claims or starting scopes, and open a decision's claim from its row. What still waits for you is in **Needs you**; the Work badge counts the same items.

When a result is waiting, open it from **Needs you**, or choose **Review** from its inspector or the queue above it. Write the reason, or pick a suggested one, and choose **Approve and next** (**⌘↵**, or **Ctrl+Enter** on other platforms) or **Return**. The next waiting item opens in its place. Trellin records approval as acceptance of the evidence and records the reviewer’s attribution.

When the inspector is narrow, open **More inspector actions** to reach actions that do not fit beside the primary action.

In Library, hover over a row or focus it with the keyboard to reveal its **Edit statement** icon. From a focused row, press **Tab** and then **Enter** to open the revision form. You can also open it from the entry inspector.

The project map, claim inspector and Claims page show the claim’s recorded review, decision and verification state. Select a claim to load its current proof task. The count covers obligations recorded in reduction routes for the current Lean statement. An obligation counts as verified while its checked proof remains current. If two routes record the same proposition, each occurrence counts in its route.

In **Work → Claims**, **Display** chooses the layout: **By domain** puts claims in one row per domain and one column per status, **Status board** groups them by status only, **List** puts each claim on its own line and **Table** aligns their fields. Claims starts by domain. Columns share the window up to a comfortable card width, and statuses with no claims other than In progress and Proved take no room, so the board scrolls sideways only in a narrow window; the domain names stay at the left while it does. Fold a domain row to one line of counts. Each card shows its code, title and one segment per proof obligation: verified, running, open or failed. **Display** also sets the density, whether proved claims show as cards or one line, and how many cards a cell shows before **Show N more**. **Filter** narrows the board by domain and status, and each active condition appears as a chip under the header; remove it with ×. **Search** matches a code, title or domain, and **New claim** opens **New theorem**. In **Work → Agent tasks**, choose **Display → Board**, **List** or **Table**; Agent tasks start in List. Each section remembers its layout on this device. Open a claim’s preview or a task’s details from any layout.

In List or Table, use the checkboxes to select items. Shift-click a checkbox to include the rows between it and the last selection. With the list or table focused, press **⌘A** (**Ctrl+A** on Windows) to select every visible item or **Esc** to clear. **Copy record IDs** puts the selected claim or Agent task IDs on the clipboard in display order.

Team opens on **Agents**. Add installed models from [Team → Runtimes](project-intake.md#connect-a-model), where **Environment check** has its own section. For a managed installation with tools outside the standard locations, the Trellin launch environment can supply colon-separated absolute directories through `RM_DESKTOP_TOOL_PATH`.

If **Proof activity** needs a newer database, choose **Back up and upgrade**. Confirm the project path in the Mac dialog. Trellin verifies a local archive containing the project files and PostgreSQL snapshot before it upgrades the selected database, then reopens the project. Keep the archive for recovery. The dialog requires this project’s saved database owner login. The backup uses PostgreSQL client tools at least as new as the server, found on `PATH` or in the usual install locations such as Homebrew's `postgresql@N`. If none is installed, the message names the version to install.

## Manage storage

The default location needs no action. Moving the library is an advanced tool, for example to keep it on another disk. Open **Trellin → Settings… → Storage** or press **⌘,** to see the active library location and open it in Finder. **Move Data…** lets you select the parent for a new **Trellin Library** folder. Review the exact old and new paths and confirm the move. Trellin copies the managed library, verifies its files, workspace IDs and existing database bindings, then switches the registered location. The original is retained as a recovery copy. An interrupted or failed check leaves the original library active. Open a managed project's new location from **Open Project…**.

The move applies to the library, its settings and the managed project files under it. External projects and explicitly exported files stay at their chosen locations. PostgreSQL keeps its existing storage and database bindings. Trellin's application support directory retains the small location record and application settings. The library itself remembers the last opened project and selected node by project ID, so moving the library keeps that state with it; any earlier macOS preference (`lastWorkspaceV1`) is migrated once and then ignored. If the library's registry or lock file is missing, Trellin rebuilds it automatically from the managed workspaces' `.trellin-managed.json` markers before opening, including after Move Data. Database logins remain in their protected configuration.

Use the existing [project backup workflow](research-foundation.md#recover-and-monitor) to preserve a project's files and PostgreSQL snapshot together. Keep app preferences and the registered library location separately when backing up Trellin's settings. A project archive can be restored as an independent project.

Open **Settings** with the gear at the bottom of the rail. Settings changes things in place: language, layout, keyboard shortcuts, updates and, in the Mac app, **Advanced**. It does not link to other pages; model connections and runtimes are in **Team**, and automation rules are in **Automation**. Search Settings for a control, for example **keyboard shortcuts**. Language and layout preferences are saved on this Mac in the app, or in the browser in a browser session. To find anything else, use the command palette (**⌘K**).

**Settings → Updates** shows the installed version and a manual check for new stable releases. Automatic checks are off by default. If enabled, Trellin checks at most once every 24 hours while the workbench is open. The check requests public release metadata from GitHub without sending project data or credentials. You can open the release notes in your browser and choose when to install the update.

## Build for local review

Use an Apple Silicon Mac with the Xcode Command Line Tools, Git and HTTPS access to the runtime and package archives. Build from a Git checkout with the compiled workbench committed. The build checks the runtime archive and wheel hashes. The output folder must already exist outside the checkout.

```sh
desktop/macos/build.sh /absolute/path/to/local-review
```

The result is an ad hoc signed `Trellin.app` for local review. By default, `build.sh` gives development builds a separate bundle identifier (`org.researchmultiplier.trellin.dev`; pass `--trial` for `org.researchmultiplier.trellin.trial`) so local and trial builds never share preferences with an installed app. Pass `--release` to build with the installed bundle identifier (`org.researchmultiplier.trellin`). To run the automated desktop checks and existing offline tests from the checkout:

```sh
python -m pytest -m offline
desktop/macos/tests/run-swift-tests.sh
```

The screens shown before the workbench opens (opening, choosing a folder, and problems such as an unavailable database) share one native component that uses the workbench's colours, type and button styles. Each problem offers one primary action that leads to a fix. Problems with a project lead back to **Open Library**, and only the folder flow offers **Open Folder…**. Each screen keeps the path and error code behind **Show Details**. To review every screen in light and dark, render them into an existing folder outside the checkout:

```sh
desktop/macos/tests/render-status-screens.sh /absolute/path/to/renders en
desktop/macos/tests/render-status-screens.sh /absolute/path/to/renders zh-Hans
```

Reference renders are in [images/desktop-status](images/desktop-status/).

## Validate the signed build

Use a fresh virtual environment outside the checkout and the app for the [installed-wheel compatibility suite](../tests/compat_validation/README.md). Install the wheel with the exact version declared in `tests/compat_validation/PACKAGE_VERSION` and keep test projects, databases and logs in disposable locations. The runner rejects a Python interpreter or package installed inside a macOS app before it starts the tests.

Use the signed app from Launchpad with an isolated application profile, a disposable data home, and [local Lean and PostgreSQL](research-foundation.md). Verify that a clean first launch creates and opens the default library, and that restarting restores the same workspace. Use a public managed case and a separate public external case to verify both database bindings and saved proof histories. In Storage settings, preview and confirm the move to an unused location. Reopen both projects and compare their IDs, evidence, and files with the original records. Verify that a rejected destination leaves the active library usable.

After quitting the app, verify the same bundle’s signature.

```sh
codesign --verify --deep --strict /absolute/path/to/local-review/Trellin.app
```
