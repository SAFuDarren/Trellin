# Project intake and theorem entry

Open the local PostgreSQL-backed workbench for the workspace. Both actions are available before any claim exists.

## Organize with AI

Choose **Organize with AI** and provide the title, subject, research objective and material you want to organize. Paste the text or select a supported text file. Choose an installed model from the running local Ollama service, then request a draft. Trellin uses only the submitted text with the on-device model.

The suggestions quote the material they came from. Review each proposed definition, hypothesis or theorem and its suggested formalization. Decide which items belong in the starting basis and supply your review details. An unresolved statement needs a formalization or can be left out. A draft can be reopened for review. Model output stays a suggestion until you confirm it. The proof workflow and its authorization remain tied to the later confirmed claim.

The list of models comes from the local Ollama service on this machine. Start that service with a supported text model available before using the button. The model choice applies to this request. This entry uses a local Ollama model and records the request with the existing intake workflow.

## Add theorem

Choose **Add theorem**, enter the natural-language wording and the exact Lean proposition, then choose its role.

- **Existing theorem** saves a library draft. You can record the source, read the saved wording and Lean statement, and confirm that exact version. Formalization confirmation records your judgment of its meaning. A checked proof has its own evidence record.
- **Target to prove** saves a human-confirmed research target. If entering a self-contained Lean proposition, write its explicit premises as binders and use the form's confirmation. Confirmed targets appear in the claims list. Lean verification and approval of extra project basis items use the existing workflows.

You can enter or review theorems while the local model is unavailable.
