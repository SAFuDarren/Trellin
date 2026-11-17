# Model budget for a research workspace

Review `research-multiplier research model-budget status` to see the applicable
UTC-month allowance and reservations in the selected project's PostgreSQL
database. Each supported structured model call in a bound workspace can reserve
its model route's approved maximum charge. This includes proof, audit, exploration,
Lean, and shadow trial calls. A deterministic check spends no model allowance.

```sh
research-multiplier research model-budget tariff LEAN_AUTHOR 0.03 \
  --max-input-bytes 48000 --max-output-tokens 4096 \
  --actor Researcher --reason "Maximum charge for this exact route and request size"
research-multiplier research model-budget limit 5 \
  --actor Researcher --reason "Monthly project allowance in USD"
research-multiplier research model-budget status
```

A route binds the exact provider, model, family and endpoint or adapter.
Use `--private` for the separately configured local model, or
`tariff PROVER --profile PROFILE …` for a named shadow profile. Enter the
documented upper charge for a request no larger than the two limits. A positive
per-call rate requires an adapter with an enforced output token bound. A
zero rate is appropriate for a free local route when evaluating model charges.

With an allowance configured, preparation refuses an unknown route or a
request outside its approved envelope before sending model inputs. Concurrent
requests share the same balance. A failed or interrupted call retains its
reservation. This accounting uses authorized upper charges, and provider-reported
usage and cost remain separate. The limit resets on the first day of the UTC
month. A researcher can change the allowance or restore open accounting with
`research model-budget limit --unlimited`.

Account-wide billing and unbound offline operations are outside this workspace
ledger. Estimate local hardware and recurring subscription costs separately.
