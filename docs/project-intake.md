# Project intake and theorem entry

Open the local PostgreSQL-backed workbench for the workspace. Both actions are available before any claim exists.

## Organize with AI

Choose **Organize with AI** and provide the title, subject, research objective and material you want to organize. Paste the text or select a supported text file. For private material, choose an installed local Ollama model and request a draft. For a public excerpt, choose a connection you added in **Team → Runtimes → Model connections**. Check the excerpt, objective and provider in the preview, then choose **Send this public excerpt**.

The suggestions quote the material they came from. Review each proposed definition, hypothesis or theorem and its suggested formalization. Decide which items belong in the starting basis and supply your review details. An unresolved statement needs a formalization or can be left out. A draft can be reopened for review. Model output stays a suggestion until you confirm it. The proof workflow and its authorization remain tied to the later confirmed claim.

### Connect a model

Choose **Team → Runtimes → Model connections → Add connection**. Select an installed Codex or Claude Code instance detected by Trellin. Sign in through the native CLI if prompted, then check again. Choose a model from the list or keep the provider default. Codex supplies its model list when supported by the installed CLI. Claude Code offers model families whose availability depends on your account. You can also enter a model identifier supported by your account. Each chosen model is a separate connection so you can select it for intake and proof work. Use **Test connection** to check account access with a short example request.

From Workbench or Chats, choose **Manage model connections** in the conversation model menu. Add the connection, close the dialog, and choose the model. Your conversation and draft stay open.

To connect directly to the OpenAI or Anthropic API, choose the API option and enter the model identifier and your API key. Trellin stores direct API keys in the macOS Keychain. The connection list contains only the models you added.

**Test connection** sends one fixed request without project material. Choose **Remove connection** to prevent future requests on that connection. A queued proof remains bound to its original connection. To run with a replacement, stop the waiting task and authorize a new task for the new connection. The project retains the prior model results and verification records. For on-device intake, start the local Ollama service with a supported text model.

## Add theorem

Choose **Add theorem**, enter the natural-language wording and the exact Lean proposition, then choose its role.

- **Existing theorem** saves a library draft. You can record the source, read the saved wording and Lean statement, and confirm that exact version. Formalization confirmation records your judgment of its meaning. A checked proof has its own evidence record.
- **Target to prove** saves a human-confirmed research target. If entering a self-contained Lean proposition, write its explicit premises as binders and use the form's confirmation. Confirmed targets appear in the claims list. Lean verification and approval of extra project basis items use the existing workflows.

You can enter or review theorems while the local model is unavailable.
