# Budget a frozen Lean goal

Public `lean search` routes share a limit of eight reserved model-call slots per frozen goal by default. The allowance follows the frozen claim and contract digest across route changes, restarts, retries, and versions of its Lean checking environment. The `--rounds` setting still controls the length of one search invocation.

```sh
research-multiplier lean budget STATEMENT_ID
research-multiplier lean budget STATEMENT_ID --history
research-multiplier lean budget STATEMENT_ID --limit 12 --actor Researcher --reason "Allow four additional slots"
```

Set an absolute total. It includes slots already reserved. A limit of zero stops new search dispatches. Lowering the limit never cancels a running proposal. A limit change requires the researcher database role and records the reason.

The database reserves a slot when a new job lease is granted, before any model call. A replay of a stored result takes no slot. A failure, expired lease, or interrupted worker retains its slot because a provider may already have received the request. Search reports `budget_exhausted` when it cannot dispatch a new proposal. Unsuccessful requests to reserve a slot do not consume job attempts.

The history shows each reservation, including earlier lease generations and missing results. Historical leases created before budgeting have no recorded dispatch time. Token and cost totals use reported measurements. Each total includes its count of reported and unknown slots. An unreported total is shown as null. The slot allowance caps the number of newly leased model attempts. A model CLI may lack a hard token cap and a subscription may not report a dollar cost.
