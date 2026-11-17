"""Execute explicitly allowed research actions in a durable dependency plan."""

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import UUID, uuid4

from research_multiplier.storage import paths
from research_multiplier.storage.postgres import Store, configured_worker_roles


_PROOF_ENV = {
    "PATH", "HOME", "SYSTEMROOT", "WINDIR", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "LC_CTYPE",
    "PGSERVICEFILE", "PGPASSFILE", "PGSSLROOTCERT", "PGSSLCERT", "PGSSLKEY",
    "RM_SCHEDULER_DSN", "RM_AGENT_DSN", "RM_VERIFIER_DSN", "RM_DATA_ROOT", "RM_ARTIFACT_ROOT",
    "RM_DOMAIN_PACKAGES",
}
_PACKAGE_PARENT = Path(__file__).resolve().parents[2]


def specification(document):
    if not isinstance(document, dict) or set(document) != {"title", "max_parallel", "budget_seconds", "steps"}:
        raise ValueError("A plan needs title, max_parallel, budget_seconds and steps.")
    steps = document["steps"]
    if not isinstance(steps, list):
        raise ValueError("Plan steps must be an array.")
    allowed = {"key", "action", "packet_id", "depends_on", "priority", "timeout_seconds", "max_attempts"}
    required = {"key", "action", "packet_id"}
    normalized = []
    for step in steps:
        if not isinstance(step, dict) or not required <= step.keys() <= allowed:
            raise ValueError("A plan step needs a key, action and reviewed packet ID.")
        dependencies = step.get("depends_on", [])
        if not isinstance(dependencies, list) or any(not isinstance(key, str) for key in dependencies):
            raise ValueError("Plan dependencies must be a list of step keys.")
        try:
            packet_id = str(UUID(step["packet_id"]))
        except (AttributeError, TypeError, ValueError):
            raise ValueError("Each plan step needs a valid packet ID.") from None
        normalized.append({
            "priority": 0, "timeout_seconds": 60, "max_attempts": 1, **step,
            "packet_id": packet_id, "depends_on": sorted(dependencies),
        })
    return {**document, "steps": normalized}


def create(document):
    with Store() as store:
        return store.query("human", "SELECT * FROM rm.create_research_plan(%s)", store.json(specification(document)))[0]


def show(identifier, *, role="human"):
    with Store() as store:
        rows = store.query(role, "SELECT * FROM rm.research_plans WHERE id=%s", identifier)
        if not rows:
            raise ValueError("No research plan matches this ID.")
        return {
            "plan": rows[0],
            "steps": store.query(role, """
                SELECT step_key, action, packet_id, claim_id, contract_sha256, packet_sha256, priority, timeout_seconds,
                    max_attempts, generation, ready_after, effective_status AS status, result, depends_on
                FROM rm.plan_work_status WHERE plan_id=%s ORDER BY priority DESC, step_key
            """, identifier),
            "returns": store.query(role, """
                SELECT step_key, generation, outcome, result, returned_at FROM rm.research_plan_returns WHERE plan_id=%s
                ORDER BY returned_at, step_key, generation
            """, identifier),
        }


def cancel(identifier, reason):
    with Store() as store:
        return store.query("human", "SELECT rm.cancel_research_plan(%s,%s) AS id", identifier, reason)[0]


def _prove(step, workspace, output):
    command = [sys.executable, "-m", "research_multiplier"]
    if workspace is not None:
        command += ["--workspace", str(workspace)]
    command += [
        "formalization", "prove", str(step["packet_id"]), "--strategy", "program", "--out", str(output),
    ]
    if step["generation"] > 1:
        command.append("--retry")
    environment = {key: value for key, value in os.environ.items() if key in _PROOF_ENV}
    for key in ("RM_DATA_ROOT", "RM_ARTIFACT_ROOT"):
        if environment.get(key):
            environment[key] = str(Path(environment[key]).expanduser().resolve())
    environment.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, errors="replace",
            timeout=step["timeout_seconds"], check=False, env=environment, cwd=_PACKAGE_PARENT,
        )
    except subprocess.TimeoutExpired:
        return "retryable", {"reason": "The proof step reached its time budget."}
    except OSError:
        return "retryable", {"reason": "The proof process could not start."}
    if completed.returncode:
        reason = completed.stderr.strip().replace(str(output), "<runs>")[-2000:]
        return ("retryable" if "already running" in reason else "failed"), {
            "reason": reason or "The deterministic proof step failed.",
        }
    try:
        receipt = str(UUID(json.loads(completed.stdout)["evidence_id"]))
    except (AttributeError, KeyError, TypeError, ValueError):
        return "failed", {"reason": "The proof process did not return an evidence ID."}
    return "completed", {"evidence_id": receipt}


def _execute(step, owner, workspace, output, *, strict):
    with paths.use_workspace(workspace, strict=strict), Store() as store:
        outcome, payload = "completed", {}
        if step["action"] == "prove":
            reused, = store.query("scheduler", "SELECT rm.plan_reusable_receipt(%s,%s,%s,%s) AS id",
                                  step["plan_id"], step["step_key"], owner, step["generation"])
            if reused["id"] is None:
                outcome, payload = _prove(step, workspace, output)
            else:
                payload = {"evidence_id": str(reused["id"])}
        # The database independently binds the returned receipt and reads the
        # current scopes, review decision and dependencies before completion.
        statement = "SELECT * FROM rm.finish_research_plan_step(%s,%s,%s,%s,%s,%s)"
        params = (step["plan_id"], step["step_key"], owner, step["generation"])
        try:
            store.query("scheduler", statement, *params, outcome, store.json(payload))
        except ValueError as error:
            # A researcher's review or scope can become stale after a lease.
            # A cancellation or replaced lease rejects the fallback as well.
            try:
                store.query("scheduler", statement, *params, "failed", store.json({"reason": str(error)}))
            except ValueError:
                return {"plan_id": step["plan_id"], "step": step["step_key"], "error": str(error)}
    return None


def worker_scope(identifiers):
    try:
        scope = list(dict.fromkeys(UUID(str(identifier)) for identifier in identifiers))
    except (TypeError, ValueError):
        raise ValueError("A worker scope needs valid UUIDs.") from None
    if not 1 <= len(scope) <= 64:
        raise ValueError("A worker scope needs 1 to 64 distinct IDs.")
    return scope


def work_group(identifiers, limit, parallel):
    scope = worker_scope(identifiers)
    if type(limit) is not int or not 1 <= limit <= 64:
        raise ValueError("Use a work limit from 1 to 64 step attempts.")
    if type(parallel) is not int or not 1 <= parallel <= 8:
        raise ValueError("Choose 1 to 8 local work slots.")
    workspace, output = paths.active_workspace(), paths.claim_runs()
    strict = paths.strict_workspace()
    pending = set()
    errors = []
    visited = set()
    launched = 0
    with Store() as store, ThreadPoolExecutor(max_workers=min(parallel, limit)) as pool:
        if strict:
            store.verify_runtime_roles(roles=configured_worker_roles("scheduler"))
        owner = uuid4()
        while pending or launched < limit:
            while len(pending) < parallel and launched < limit:
                leased = store.query("scheduler", "SELECT * FROM rm.lease_research_plan_group(%s,%s)", scope, owner)
                if not leased:
                    break
                pending.add(pool.submit(_execute, leased[0], owner, workspace, output, strict=strict))
                visited.add(leased[0]["plan_id"])
                launched += 1
            if not pending:
                break
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for finished in done:
                error = finished.result()
                if error is not None:
                    errors.append(error)
    return {"plans_visited": len(visited), "attempts_started": launched, "worker_errors": errors}


def work(identifier, limit):
    plan = show(identifier, role="scheduler")["plan"]
    result = work_group([identifier], limit, plan["max_parallel"])
    return {**show(identifier, role="scheduler"), "attempts_started": result["attempts_started"],
            "worker_errors": result["worker_errors"]}
