# Durable research plans

A plan schedules explicitly selected actions against already confirmed
formalization packets. Different ready steps can execute concurrently, with
the database enforcing the plan's parallelism limit across workers. A
plan never approves or promotes evidence.

## Create a plan

This example runs the deterministic checker, waits for the researcher to
review the exact receipt produced by that proof step and records a snapshot
of the reviewed claim. Substitute the same confirmed packet ID in each step.

```json
{
  "title": "Verify and review a candidate",
  "max_parallel": 2,
  "budget_seconds": 145,
  "steps": [
    {"key": "proof", "action": "prove", "packet_id": "PACKET_ID", "priority": 10,
     "timeout_seconds": 120},
    {"key": "review", "action": "await-review", "packet_id": "PACKET_ID",
     "depends_on": ["proof"], "timeout_seconds": 10},
    {"key": "record", "action": "snapshot", "packet_id": "PACKET_ID",
     "depends_on": ["review"], "timeout_seconds": 15}
  ]
}
```

Run the following from the same project workspace for both workers and
reviewers. Use the ID returned by `create`.

```sh
research-multiplier plan create plan.json
research-multiplier plan work PLAN_ID
research-multiplier plan show PLAN_ID
```

The `prove` action accepts [registered deterministic domains](domain-packages.md)
whose admitted checker has a deterministic solver enabled for background proof.
Its subprocess has a deadline set by `timeout_seconds`. Each step can
declare `max_attempts` for retryable failures. The sum of the declared
step budgets multiplied by their attempt ceilings must fit the plan budget.
Waiting for a researcher uses neither an attempt nor a worker slot.
Independent steps can target different approved packets. The scheduler
selects higher-priority ready steps first.

Use `review-evidence` to record a human decision, then run `plan work`
again. An approval enables successor steps. A return is recorded on the
review step and prevents successor steps that need the approval. Changes
to the current evidence can make previously completed prerequisites
require rechecking. A snapshot reports claim status and evidence state
when it was recorded.

Use `plan cancel PLAN_ID --reason "Changed direction"` to cancel
unfinished steps. This fences completion of leased plan actions. An
already dispatched deterministic task can still commit its own result to
the existing evidence workflow.

## Run a worker across plans

```sh
research-multiplier plan worker --plan FIRST_PLAN --plan SECOND_PLAN --parallel 2 --watch 5
```

The worker takes ready steps only from the listed plans. The database ranks
those steps by priority across plans and assigns each lease once while
enforcing each plan's parallelism and attempt limits. A running proof
holds the proof slot for its frozen claim across plans. A subsequent step
can use a current exact receipt for the same formalization instead of
restarting the proof process. Waiting for human review, stale premises,
exhausted attempts, and cancelled plans leave no new work to lease.

Use the scheduler login for the worker. A step that has to generate and
verify a new deterministic candidate also uses the agent and verifier
logins through the existing proof process. Receipt reuse, review and
snapshot steps run with the scheduler login alone.
