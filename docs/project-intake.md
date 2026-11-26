# Project intake and theorem entry

Open the local PostgreSQL-backed workbench for the workspace. The **+** button in the project map header (**Add to project**) has both entries, and they are available before any claim exists.

## Import paper, notes or code

Choose **+ → Paper, notes or code…**, or drop a text, Markdown, TeX or Lean file on the project map. A project without claims shows a drop area in the middle of the map, with **Choose file…** and **New theorem…**. The dialog opens on **Choose file…** and **Paste text instead**; a file dropped on the map opens it with that file in place. With the material in place, the dialog names the file and its size and the model that will read it, then **Organize** sends it. A file the import cannot read, such as a PDF or one over 18 KB, says so in the dialog.

The title comes from the file name, the research objective uses a default, and the model is the one last used for this project, or the first available one. **Advanced options**, remembered per project, holds the title, model, research objective and the editable text. Without a usable model the dialog says so and links to **Team → Runtimes → Model connections**.

A local Ollama model reads private material on this Mac. The first time a project uses a connected cloud model for importing, the dialog asks once to allow that model for this project's imports. Trellin records who allowed it and when, and later imports with that model are sent without asking again. **Team → Runtimes** shows **Allowed for this project's imports** beside the connection; **Withdraw** makes the next import ask again. Removing or changing the connection also ends the permission. Only send material you can share with that provider.

The suggestions quote the material they came from. Review each proposed definition, hypothesis or theorem and its suggested formalization. Decide which items belong in the starting basis and supply your review details. An unresolved statement needs a formalization or can be left out. A draft can be reopened for review. Model output stays a suggestion until you confirm it. The proof workflow and its authorization remain tied to the later confirmed claim.

### Connect a model

Choose **Team → Runtimes → Model connections → Add connection**. Select an installed Codex or Claude Code instance detected by Trellin. Sign in through the native CLI if prompted, then check again. Choose a model from the list or keep the provider default. Codex supplies its model list when supported by the installed CLI. Claude Code offers model families whose availability depends on your account. You can also enter a model identifier supported by your account. Each chosen model is a separate connection so you can select it for intake and proof work. Use **Test connection** to check account access with a short example request.

From Workbench or Chats, choose **Manage model connections** in the conversation model menu. Add the connection, close the dialog, and choose the model. Your conversation and draft stay open.

To connect directly to the OpenAI or Anthropic API, choose the API option and enter the model identifier and your API key. Trellin stores direct API keys in the macOS Keychain. The connection list contains only the models you added.

**Test connection** sends one fixed request without project material. Choose **Remove connection** to prevent future requests on that connection. A queued proof remains bound to its original connection. To run with a replacement, stop the waiting task and authorize a new task for the new connection. The project retains the prior model results and verification records. For on-device intake, start the local Ollama service with a supported text model.

## New theorem

Choose **+ → New theorem…**, enter the natural-language wording and the exact Lean proposition, then choose its role.

- **Existing theorem** saves a library draft. You can record the source, read the saved wording and Lean statement, and confirm that exact version. Formalization confirmation records your judgment of its meaning. A checked proof has its own evidence record.
- **Target to prove** saves a human-confirmed research target. If entering a self-contained Lean proposition, write its explicit premises as binders and use the form's confirmation. Confirmed targets appear in the claims list and as independent goals on the current project's Proof canvas. The current claim stays open. Select another goal on the canvas to open its proof graph. Use **File → New Project** in the Mac app to start a separate project. Lean verification and approval of extra project basis items use the existing workflows.

You can enter or review theorems while the local model is unavailable.
