# Changes

## 1.0.109

- Settings changes things in place and no longer sends you to other pages: model connections and runtimes stay in **Team**, automation rules in **Automation**. The gear opens Settings in one click, and **Help** has its own icon at the bottom of the rail. Storage notes say "on this Mac" in the app.

## 1.0.108

- **Home** opens a full-width project map with connection status beside the zoom controls. **Claims** is under **Work**, and existing Claims links still work.
- Space panels and page headers use the same layout. Resize the navigation panel once and its width carries across spaces.

## 1.0.107

- The left rail now has five spaces, as in Multica: **Home** (project map and Claims), **Work** (Inbox, Review, Agent tasks and the Library), **Chats**, **Team** and **Automation**. Each space's panel lists its pages, and pages no longer repeat an entry the panel already has.
- Projects are switched and created from the tile at the top of the rail. **Open Folder** and the library location moved to **Settings → Advanced**. The Switch view and Refresh buttons are gone from the sidebar header.

## 1.0.106

- The screens Trellin shows before the workbench opens now match the workbench: a centred panel with an icon for the kind of problem, one primary action that fixes it, and the path and error code behind **Show Details**. Problems with a project lead back to **Open Library**; opening another folder is offered only after you chose one.

## 1.0.105

- Trellin no longer stops at "Cannot open the Trellin library" when the last project cannot be reopened, for example a project saved by another Trellin installation. It opens your library instead and shows a short notice with **Open Project…**. The last project is now reopened by its ID through the active library.

## 1.0.104

- The project sidebar starts folded when there is only one project or the window is narrower than 1,200 px, so the map keeps room beside the inspector. Opening the inspector refits the map until you pan or zoom.
- Selecting rows in Claims and Agent work no longer moves the list: the selection bar floats over the bottom edge.
- Primary actions that open another page no longer show a link underline, and canvas handles and legend toggles have full-size hit areas.
- A browser check covers every page at three window sizes in light and dark for clipped text, hidden cards and other layout faults.

## 1.0.103

- The owner merge command checks the current PR claim, patch version and required checks on the reviewed commit before merging.

## 1.0.102

- Maintainers can preview and retire recoverable merged branch heads. A weekly GitHub report tracks pending branch reviews.

## 1.0.101

- Contributor issues record difficulty, model fit and verification requirements. The contribution guide documents fork-based claims and the model score reference.

## 1.0.100

- Agent work has an Inbox for pending reviews, questions, failed runs and Agent deliveries. Preview each item, mark it read or archive it, and undo the last change while the project stays open in this window.

## 1.0.99

- Select several claims or Agent tasks in List and Table. Shift-click or use the keyboard to select displayed rows, then copy their exact record IDs.

## 1.0.98

- Proof headers show the selected node’s place in the claim, with links back to its goal and the project map. Agent handoff details link to the original delivery in their header.

## 1.0.97

- Claims and Agent work can switch among Board, List and Table. Each page remembers its layout on this device, with the same recorded details and actions available in each view.

## 1.0.96

- Open a saved Agent’s name to view its project activity, edit its existing instructions, check the model connection, assign a task, or continue chatting.

## 1.0.95

- Settings groups the existing controls and links. Search in English or Chinese to jump to the matching setting or open its page.

## 1.0.94

- The Settings and Help menu uses a gear icon and shows a Settings tooltip.

## 1.0.93

- When dragging a research link, the project map marks both points where the link will attach. Keyboard focus uses a thin outline inside the map.

## 1.0.92

- Navigation, project and chat selections and selected Library rows now use a soft background fill. Keyboard focus keeps its own outline.

## 1.0.91

- In narrow proof windows, the withdrawal inspector gives affected records room to show their full names and places the count below the heading. The preview bar stays above the graph.

## 1.0.90

- The proof graph keeps status names and counts readable across window sizes. When the graph is too narrow for the labels, open them from the Status button.

## 1.0.89

- Agent chat opens in the same lower-right floating panel across work areas, clear of the inspector and canvas controls. Resize its edges or corner, use the keyboard, or maximize and restore. Trellin remembers your preferred size.

## 1.0.88

- Agent work, Claims and Review have separate destinations in the rail. Agent work includes the project activity list, and task details scroll below their header. Existing Agent work links still open the same task.

## 1.0.87

- Inspector tags and status labels wrap within the pane, and actions that need more room are available in **More inspector actions**.

## 1.0.86

- Dragging a research link previews its saved route over the target card. Select a link to see both claim names without covering cards. Use Undo after removing a link to restore it.

## 1.0.85

- The graph and inspector report the same withdrawal-impact total across claims and mark the selected premise as the preview target.

## 1.0.84

- The proof graph’s References view highlights recorded proof uses and allowed premises, shows planned steps as quieter dashed lines, and explains the line styles in English and Chinese. The legend follows the current view.

## 1.0.83

- Narrow proof and map windows use a details drawer and a separate zoom footer. The selected proof node and nearby map cards stay in view when the inspector opens or the window changes size.

## 1.0.82

- Open a verified result directly from its inspector, review queue or bottom-bar review count. Activity and review lists show the result and its claim, and Command/Control+Enter records the chosen decision with its reason. (#215)

## 1.0.81

- Proof node order and reference codes stay consistent when switching the interface language.

## 1.0.80

- Proof nodes show running jobs as a spinner and count inside the card. Selection has a separate outline, and the spinner stays still with reduced motion enabled.

## 1.0.79

- Contributors can run `python scripts/tasks.py branches` to read branch lifecycle, PR evidence, and worktree status without changing Git references.

## 1.0.78

- Selected map cards, proof nodes and review rows use a Navy outline and tint, with separate hover and keyboard-focus states. The light project map has a more visible dotted grid.

## 1.0.77

- Help includes short guides for the project map, Agent work and proof review. All guide links open real pages inside Trellin, and the window and Help use the Trellin name in both languages.

## 1.0.76

- Empty Agent chat and activity panels size to their contents. Start with a draft suggestion or open Agent work from the compact panel.

## 1.0.75

- Draw research links from claim handles, select links directly for removal, and use the same actions from the keyboard (#196).

## 1.0.74

- Press **?** to see keyboard shortcuts. Choose **Move card** from a project card’s context menu to preview its new position with the arrows. Enter saves the position and Escape cancels.

## 1.0.73

- Agent chat opens below the workbench graph and suggests drafts for the selected claim or proof node. A checked history control keeps earlier messages out of the outgoing request while preserving the conversation.

## 1.0.72

- Double-clicking a project map card opens its proof even when the inspector starts closed in a narrow window. A single click highlights the card before the inspector opens.

## 1.0.71

- Search proof nodes by title from the command palette. The open claim appears first, followed by other recent claims. Menus, tooltips and Help display shortcuts for the current platform.

## 1.0.70

- Proof graphs open at a readable zoom and can fit all recorded nodes in the window. Node cards wrap titles and show activity counts. The inspector shows complete formulas, Lean statements and recorded work details. Open Edge styles to read the legend.

## 1.0.69

- Keep the proof task first in the inspector and move secondary actions into a menu when needed. Double-click or press Enter on a project card to open its proof, and keep adjacent cards in view when resizing the inspector (#198).

## 1.0.68

- The project map, Claims board and inspector explain when no proof obligations are recorded. Proved claims keep their proof status, and result links open the exact records awaiting review.

## 1.0.67

- Open Agent activity to see recorded proof and model jobs, Agent work, conversations and results to review in one list. Filter by state and open the original work from each row. The footer, Team, Claims and Chats share the same activity counts.

## 1.0.66

- Light and dark themes use the Trellin Navy accent for selection, focus, primary actions, and running work. Page, section, and card headings have distinct sizes. Team opens on Agents, and Environment check has its own section under Runtimes.

## 1.0.65

- Library rows show an edit icon on hover or keyboard focus. The table and inspector use monospace for Lean declarations and the body font for plain-language names.

## 1.0.64

- The command palette shows icons and keyboard hints. Use `#` to find claims and their proof references, or `@` to find Agents. Map and proof menus display shortcuts for actions that support them.

## 1.0.63

- Each workbench area gives its main action one filled button. The project map uses icon toggles for Details and Global map, with keyboard shortcuts and visible pressed states. Find claim uses the same outlined style as neighbouring actions.

## 1.0.62

- Claim details lead with the title, followed by the current state and actions. Cards omit repeated column states and use an empty progress mark when a claim has no recorded obligations. The project map heading uses the registered project name or the opened folder name.

## 1.0.61

- Start typing in Agent chat or Chats immediately. Choose a recipient in the composer with @, use a starter to fill a draft, and carry the same thread draft between the dock and Chats.

## 1.0.60

- Open a claim’s proof in the main canvas from its details, context menu, or the command palette. Return to the same project map and camera with Escape. On Mac, a library with one project starts with the project sidebar folded.

## 1.0.59

- Project map cards, the claim inspector, and Claims show the verified and total recorded obligations for the current statement, alongside the current review, decision or verification state.

## 1.0.58

- Press ⌘K or Ctrl+K from any workbench page to find claims, Lean proof references, Library entries, conversations and Agents. Pick a recent item or open an existing research action with the same confirmation steps.

## 1.0.57

- Right-click a project map card or proof node to use its research actions, copy a saved reference or Lean statement, and find related Library sources. Shift+F10 also opens the menu.

## 1.0.56

- Search for claims, Agents, conversation tasks and models from the active page. Workbench menus open beside their controls and support arrow keys, typeahead, Escape and focus return.

## 1.0.55

- Workbench controls and project cards show consistent hover, press and selection states. The claim inspector and Global map use the shared entrance timing, and floating controls share one shadow.

## 1.0.53

- Buttons, cards, list rows and menus ease between states instead of jumping. Menus, the Agent dock and dialogs fade in, and corners and shadows follow one scale. With reduced motion turned on in macOS, nothing moves.

## 1.0.52

- **Global map** opens in a corner of the project map that leaves the claim cards visible, or at its compact size without covering the selected claim when the window is small. Claims that have no recorded steps yet appear as separate points in the overview.

## 1.0.51

- Dropdowns in forms, filters and the proof note composer show their whole selected option. They share one flat style with a theme-aware arrow, and form dropdowns line up with the text fields beside them.

## 1.0.50

- In Chats, switch a saved Agent’s tasks from the task title in the header. The menu shows the current task and its recorded state. Reopen earlier unassigned conversations and proof activity from Conversation history.

## 1.0.49

- Keep the project map visible while inspecting Lean proofs, proof tasks, and review. Open Agent Chat from any page or attach the selected proof node explicitly from its details. Use Activity to check saved Agent tasks and their replies.

## 1.0.48

- Assign project work to a saved Team agent. Preview the approved inputs, hand the recorded delivery to another agent, and accept or return the result from Agent work. Database revision `0083` keeps assignments, attempts, and decisions available after reopening.

## 1.0.47

- Select a claim on the project map to read its details and review queue alongside the canvas. Open **Global map** to explore the existing overview of recent claims and proof relationships.

## 1.0.46

- Open Workbench on a project map with a project-only sidebar. Select claims in place, drag them to arrange the map, save research links, and open the selected proof with a separate action. Related research commands share one menu.

## 1.0.45

- Open Chats by employee, switch between their named tasks, and see which tasks are waiting for a reply or need attention. Follow-up messages use the employee's saved connection. Drafts stay with their task while switching in Chats.

## 1.0.44

- Create and edit named local agent profiles in Team using existing model connections. Profiles reopen in their own project with their saved identity and instructions.

## 1.0.43

- Use consistent heading, message, control and status text sizes across Workbench, Chats and Proof activity. Keep graph modes and long labels visible when the side panels narrow the canvas.

## 1.0.42

- Open registered local projects from the Mac Workbench sidebar and restore each project's selected graph node when switching back.

## 1.0.41

- Send chats and queue eligible proof notes with Enter or one click, keep Shift+Enter for new lines, and see sending, waiting and recorded delivery status.

## 1.0.40

- Reopen approved research questions and look up related work in the current project before preparing conjectures.

## 1.0.39

- Make the intake response format explicit so connected models can produce reviewable drafts.

## 1.0.38

- Show when a selected Claude Code model is unavailable for the configured account.

## 1.0.37

- Save and reopen private proof-note drafts for individual nodes, including while delivery is blocked.

## 1.0.36

- Review the approved basis and compare and confirm saved conjectures in Workbench.

## 1.0.35

- Return to the selected graph node from Review and open Settings or Help from the rail menu.

## 1.0.34

- Update the web development tools to address reported advisories.

## 1.0.33

- Propose and review a premise in Workbench, with explicit confirmation of a separate revised claim.

## 1.0.32

- Verify scoped local lemma plans in Workbench and show checked lemmas in the proof graph.

## 1.0.31

- Record repository handover verification and align guidance on candidate versions.

## 1.0.30

- Configure scheduled dependency updates for Python, the web interface, and GitHub Actions.

## 1.0.29

- Show whether each proof note is waiting, blocked, stale, or included in a proof attempt.

## 1.0.28

- Keep newly added proof goals on the active project canvas.

## 1.0.27

- Manage model connections from Workbench and Chats without losing the current draft or conversation.

## 1.0.26

- Show open issues in urgency and pickup order on the task board.

## 1.0.25

- Check the next package version against main before merging.

## 1.0.24

- Choose a specific Codex or Claude Code model in Team. Direct model conversations
  work in Chats before the first claim and in the graph beside the selected node.
- Send messages directly from a shared Workbench and Chats composer. Pick a
  model in the app, inspect request details when needed, and resume conversations
  when reopening the workspace.
- Browse conversations beside their message history, with a fixed input area.
  Team groups model connections, recorded activity, jobs, runtimes and usage.
  Both views use neutral gray workbench surfaces.
- Leave a node-specific draft in Proof activity. On database revision `0081`,
  release an exact public note for a later authorized Codex proof task and
  see the attempt that received it.
- Use **Back up and upgrade** in Proof activity to enable Team Codex guidance
  on an older Mac project after verifying its archive. Selected navigation
  rows and proof activity tabs use a flat gray background.
- Use a compact icon rail with keyboard and hover labels. Claim and section
  lists remain in the sidebar.
- Expand a Claims card in place, keep long graph labels inside their nodes, and
  see the recorded Library state in both the table and inspector.
- Drag the corner of a proof graph node to resize it horizontally and vertically.
- Screen readers can follow graph nodes to the inspector, read the overview
  resize range and current width, and navigate Help definitions.
- Edit an entry from the Library table or inspector and review both statements
  before confirming a new version. Frozen claims retain their original evidence.

- Add, test and remove user-selected Codex, Claude Code, OpenAI and Anthropic
  connections under Team → Runtimes. Detect installed Codex and Claude Code commands
  and reuse their existing logins. Store direct API keys in the macOS Keychain.
- Use a connected model to organize an explicitly reviewed public excerpt
  and save the result for the existing intake review.
- Authorize a selected connection for bounded proof work on a released
  Lean scope. Review the model and its reported usage alongside local verification.

## 1.0.23

- Document repository layout, task branch retirement, and issue pickup by urgency.

## 1.0.22

- Open a default Trellin library on first launch and keep new managed projects
  together under the user's application support directory.
- Move the library from the Mac app's Storage settings after confirming the
  destination and checking the copied files and database bindings. Keep the
  original copy available for recovery and leave external projects in place.

## 1.0.21

- Preview a project or selected claims and export a printable HTML and
  editable Markdown handoff from the same confirmed snapshot.
- Find the latest checksum-verified project backup through the existing
  backup and restore flow.

## 1.0.20

- Show the installed version in Trellin and offer a manual stable-release
  check. Optional daily checks share one setting across workspaces and
  stay off until enabled. Read release notes from the update notice.

## 1.0.19

- Introduce the Trellin workbench with English and Chinese getting-started
  pages, annotated proof-graph screenshots, and source installation links.

## 1.0.18

- Answer formalization questions in Trellin, prepare and inspect the exact
  revised Lean statement, and confirm it before moving on. Saved answers
  and proposals stay available after reopening the workspace.

## 1.0.17

- Add a documented v1 claim-list endpoint and generated types while keeping
  the previous claim list available. Versioned claim reads include a request
  ID and a log entry with route, status and duration.

## 1.0.16

- Attribute remaining human research decisions and policy records to the
  authenticated local researcher. The legacy review route also resolves the
  signed-in user after upgrade and accepts its prior payload.
- Database revision `0077` follows `0076` and preserves historical authorship.

## 1.0.15

- Update workbench views for clarifications, agent conversations and other
  committed workspace changes. While the complete stream is connected, the
  views update from events. Periodic refresh resumes during interruptions.

## 1.0.14

- Record clarification, agent activity, intake, library and other committed
  workspace changes in the existing resumable event stream.
- Database revision `0076` follows `0075`. Existing records and event cursors
  carry forward through the migration.

## 1.0.13

- Refresh open research views for confirmed scopes, withdrawals and proof task
  changes, and replay the same updates after reconnecting.

## 1.0.12

- Stream committed confirmations, withdrawals, proof task state and checked
  evidence from the workspace database, including work started from the CLI.

## 1.0.11

- Refresh open workbench views when evidence reviews are committed and replay
  missed review events after a connection interruption.

## 1.0.10

- Record new evidence reviews in the database transaction and expose a local
  event stream that resumes after the last received event.

## 1.0.9

- Record a stable local researcher ID on new approval records. The versioned
  workbench review endpoint resolves identity from the selected workspace and
  returns the original decision on a repeated request.

## 1.0.8

- Save stable claim and proof-node references in PostgreSQL. Renamed prefixes
  keep resolving to the original records, and activity displays recorded model
  reasoning effort.

## 1.0.7

- In Trellin, authorize proof work on a confirmed Lean scope, see its recorded
  progress and verification result, and stop the selected task.

## 1.0.6

- CI checks for new reverse imports between entry points and shared workflows.

## 1.0.5

- The full compatibility suite requires a separate installed wheel environment
  and rejects Python or package metadata inside signed Trellin builds.

## 1.0.4

- Document the release recovery drill and record a tested rollback between
  installed versions.

## 1.0.3

- Project restore checks the PostgreSQL connection format before writing to
  the target, so users can correct the configuration and retry safely.

## 1.0.2

- The Task link pull-request check uses the effective issue claim and rejects
  blocked or unclaimed work issues.

## 1.0.1

- Build and open Trellin on Apple Silicon Macs. Pick an existing research
  workspace and return to its last graph node when reopening the app.
- Trellin identifies the workspace and missing setup when a saved project
  cannot open, and offers another folder from the same window.
- Open a research folder in Trellin. On first open it sets up a `Trellin`
  subfolder with its own local PostgreSQL database.
- Trellin uses the Trellin logo for its app icon and on the screen shown while
  it opens a workspace; the workbench shows it as the browser tab icon.
- Organize selected project notes with an on-device model before the first
  claim, and enter a known Lean theorem or a target to prove by hand.

## 1.0.0

First public release of Research Multiplier, a local workspace for AI-assisted
theoretical research.

- Choose a research workspace, record an offline research brief, and replay
  supported deterministic checks without a model or database.
- Freeze an exact research statement and its approved basis. Use Lean 4.19.0
  and Mathlib to check structured proofs, `.lean` source files, or Lake
  declarations against the confirmed scope and record their actual dependencies.
- Save verified working lemmas, reuse applicable proofs within a workspace,
  and mark downstream evidence for recheck when an input is withdrawn.
- Advance supported proof routes under a budget with optional model adapters.
  Search local Formalpedia snapshots, use the research inbox, and resume work
  from the recorded checkpoint.
- Follow the separate first-proof walkthrough from installation through
  confirmation, verification, replay, and withdrawal.
- Open a local proof workbench (`workbench`) to inspect the dependency graph,
  exact Lean statements, open obligations, the impact of withdrawing an
  assumption, and live model activity, and to leave guidance for models. A
  side bar opens the review inbox (approve or return results), claims, the
  theorem library, model conversations, team runtimes and jobs, and
  automation policies. The interface is in English with a complete Chinese
  translation, chosen in Settings.
- `init-workspace` and `setup` write an `AGENTS.md` guide for coding agents into
  each research workspace.
- Setup can initialize a local PostgreSQL project through a Unix socket.
- Licensed under the Apache License 2.0. Contributions are accepted under the
  Developer Certificate of Origin; the project name and logo follow
  `TRADEMARKS.md`.
