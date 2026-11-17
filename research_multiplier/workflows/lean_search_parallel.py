"""Pin the requests of one route pass before running their model calls."""

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

from research_multiplier.workflows import runtime
from research_multiplier.protocol.serialization import fingerprint


def operation_keys(policy_id, statement, history):
    batch = fingerprint({"policy": str(policy_id), "statement": statement["statement_sha256"], "history": history})
    return f"lean-parallel-v1:{batch}"


def recorded(store, artifacts, subject, prefix, route, limit):
    first = f"{runtime.HARNESS}:{prefix}:1"
    rows = store.query("verifier", """
        SELECT payload -> 'parallel_batch' AS batch FROM rm.jobs
        WHERE claim_id = %s AND contract_sha256 = %s AND kind = 'prove'
          AND (operation_key = %s OR starts_with(operation_key, %s))
        ORDER BY enqueued_at, id LIMIT 1
    """, subject["claim_id"], subject["contract_sha256"], first, f"{first}:budget:")
    if not rows:
        return None
    batch_ref = rows[0]["batch"]
    batch = artifacts.read_json(batch_ref)
    if (not isinstance(batch, dict) or batch.get("route") != route or type(batch.get("version")) is not int or batch["version"] != 1
            or not isinstance(batch.get("requests"), list) or not 1 <= len(batch["requests"]) <= limit):
        raise ValueError("The saved parallel Lean requests require their exact approved scope and author settings.")
    return batch_ref, [artifacts.read_json(reference) for reference in batch["requests"]]


def pin(artifacts, route, requests):
    return artifacts.put({"version": 1, "route": route, "requests": [artifacts.put(request) for request in requests]})


def run(entries, slots, propose):
    """Propose in parallel and preserve the prepared order for proof admission."""
    if len(entries) == 1 or slots == 1:
        return [propose(entry) for entry in entries]
    with ThreadPoolExecutor(max_workers=min(slots, len(entries))) as workers:
        futures = [workers.submit(copy_context().run, propose, entry) for entry in entries]
        return [future.result() for future in futures]
