"""Advance signed research policies using stored plans and verifier observations."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from contextvars import copy_context
from time import monotonic_ns
from uuid import UUID

from research_multiplier.workflows import research_plans
from research_multiplier.workflows.runtime import VerificationQueueError
from research_multiplier.storage.postgres import DatabaseOperationError, Store
from research_multiplier.protocol.serialization import json_text
from research_multiplier.storage.lean_progress import PendingLeanPass, hold_lean_pass, pending_lean_passes

_METRICS = ("exact-closure", "flow-interval-width")
_ACTIONS = ("prove", "await-review", "snapshot")


def route(packet_id):
    with Store() as store:
        rows = store.query("human", "SELECT * FROM rm.choose_research_route(%s)", packet_id)
        if not rows:
            raise ValueError("No frozen formalization packet matches this ID.")
        choice = rows[0]
        if choice["domain"] == "lean-core" and choice["route"] == "observe_verified_evidence":
            anchors = store.query("human", """
                SELECT id FROM rm.lean_statement_status WHERE packet_id = %s AND check_ready
                  AND body ->> 'theorem' = 'RMTheory.expected'
            """, packet_id)
            if anchors:
                return {**choice, "route": "lean_advancement",
                        "reason": "Try ready Lean reductions and reusable proofs using the confirmed scope.",
                        "suggested_policy": specification({
                            "packet_id": str(packet_id), "metric": "exact-closure", "actions": ["lean"],
                            "lean_statement_id": str(anchors[0]["id"]),
                        })}
    recommendation = None
    if choice["route"] in ("deterministic_proof", "observe_verified_evidence"):
        recommendation = specification({
            "packet_id": str(packet_id),
            "metric": "flow-interval-width" if choice["domain"] == "fixed-directed-max-flow" else "exact-closure",
            "actions": list(_ACTIONS) if choice["route"] == "deterministic_proof" else [],
        })
    return {**choice, "suggested_policy": recommendation}


def specification(document):
    if not isinstance(document, dict):
        raise ValueError("A progress policy must be an object.")
    if document.get("actions") == ["lean"]:
        allowed = {"packet_id", "metric", "actions", "lean_statement_id", "lean_model_rounds", "priority",
                   "lean_model_scope", "lean_route_parallelism", "lean_route_stall_limit"}
        required = {"packet_id", "metric", "actions", "lean_statement_id"}
        if not required <= document.keys() <= allowed or document["metric"] != "exact-closure":
            raise ValueError("A Lean policy needs an exact-closure metric, packet and confirmed Lean statement.")
        rounds, priority = document.get("lean_model_rounds", 0), document.get("priority", 0)
        if type(rounds) is not int or not 0 <= rounds <= 8 or type(priority) is not int or not -100 <= priority <= 100:
            raise ValueError("Choose 0 to 8 model rounds and a priority between -100 and 100.")
        route_width = document.get("lean_route_parallelism", 1)
        stall_limit = document.get("lean_route_stall_limit", 3)
        if type(route_width) is not int or not 1 <= route_width <= 3 or (route_width > 1 and route_width > rounds):
            raise ValueError("Choose one to three Lean routes within the signed model allowance.")
        if type(stall_limit) is not int or not 1 <= stall_limit <= 3 or (route_width == 1 and stall_limit != 3):
            raise ValueError("Choose one to three no-progress checkpoints for parallel Lean routes.")
        model_scope = document.get("lean_model_scope", "public")
        if model_scope not in ("public", "local") or (model_scope == "local" and rounds == 0):
            raise ValueError("Local model authorization requires at least one permitted round.")
        try:
            packet = str(UUID(str(document["packet_id"])))
            anchor = str(UUID(str(document["lean_statement_id"])))
        except ValueError:
            raise ValueError("A Lean policy needs exact packet and Lean statement IDs.") from None
        return {**document, "packet_id": packet, "lean_statement_id": anchor,
                "lean_model_rounds": rounds, "priority": priority}
    allowed_keys = {
        "packet_id", "metric", "actions", "max_rounds", "proof_timeout_seconds",
        "proof_max_attempts", "budget_seconds", "priority",
    }
    if not {"packet_id", "metric", "actions"} <= document.keys() <= allowed_keys:
        raise ValueError("A progress policy needs packet_id, metric and actions.")
    if document["metric"] not in _METRICS or document["actions"] not in ([], list(_ACTIONS)):
        raise ValueError("Select a supported metric and the complete automatic route or an observation-only policy.")
    automatic = bool(document["actions"])
    defaults = {
        "max_rounds": int(automatic),
        "proof_timeout_seconds": 60 if automatic else 0,
        "proof_max_attempts": int(automatic),
        "priority": 0,
        **document,
    }
    budget_fields = ("max_rounds", "proof_timeout_seconds", "proof_max_attempts", "priority")
    if any(type(defaults[field]) is not int for field in budget_fields):
        raise ValueError("Progress budgets, rounds and priority must be integers.")
    if "budget_seconds" not in defaults:
        defaults["budget_seconds"] = defaults["max_rounds"] * (
            defaults["proof_timeout_seconds"] * defaults["proof_max_attempts"] + 2
        )
    if type(defaults["budget_seconds"]) is not int:
        raise ValueError("The progress budget must be an integer.")
    return defaults


def propose(document, *, store=None):
    policy = specification(document)
    with (Store() if store is None else nullcontext(store)) as store:
        if policy["actions"] == ["lean"]:
            bound = store.query("human", "SELECT packet_id FROM rm.lean_statement_status WHERE id = %s",
                                policy["lean_statement_id"])
            if not bound or str(bound[0]["packet_id"]) != policy["packet_id"]:
                raise ValueError("The Lean statement must belong to the selected packet.")
            private = None
            if policy.get("lean_model_scope") == "local":
                from research_multiplier.model_adapters import ModelConfig
                private = ModelConfig.from_env("LEAN_AUTHOR", private=True).private_provenance()
            routes = policy.get("lean_route_parallelism", 1)
            if routes > 1:
                return store.query("human", "SELECT * FROM rm.propose_lean_route_progress(%s,%s,%s,%s,%s,%s)",
                                   policy["lean_statement_id"], policy["lean_model_rounds"], policy["priority"], routes,
                                   policy.get("lean_route_stall_limit", 3), store.json(private) if private else None)[0]
            if private is not None:
                return store.query("human", "SELECT * FROM rm.propose_private_lean_progress(%s,%s,%s,%s)",
                                   policy["lean_statement_id"], policy["lean_model_rounds"], policy["priority"],
                                   store.json(private))[0]
            return store.query("human", "SELECT * FROM rm.propose_lean_progress(%s,%s,%s)",
                               policy["lean_statement_id"], policy["lean_model_rounds"], policy["priority"])[0]
        return store.query("human", "SELECT * FROM rm.propose_research_progress(%s)",
                           store.json(policy))[0]


def approve(policy_id, fingerprint, actor, reason, *, store=None):
    with (Store() if store is None else nullcontext(store)) as store:
        return store.query("human", "SELECT rm.approve_research_progress(%s,%s,%s,%s) AS id",
                           policy_id, fingerprint, actor, reason)[0]


def stop(policy_id, reason, *, store=None):
    with (Store() if store is None else nullcontext(store)) as store:
        return store.query("human", "SELECT rm.stop_research_progress(%s,%s) AS id", policy_id, reason)[0]


def status(policy_id=None):
    query = """
        SELECT p.*, c.status AS claim_status, c.recorded_status AS recorded_claim_status, c.interpretation_hold,
               last_run.result AS lean_run
        FROM rm.research_progress_status p JOIN rm.claim_foundation_status c ON c.id = p.claim_id
        LEFT JOIN rm.lean_progress_last_run last_run ON last_run.policy_id = p.id
    """
    with Store() as store:
        if policy_id is None:
            return store.query("human", query + " ORDER BY p.proposed_at DESC")
        policies = store.query("human", query + " WHERE p.id = %s", policy_id)
        if not policies:
            raise ValueError("No research progress policy matches this ID.")
        return {
            "status": policies[0],
            "rounds": store.query(
                "human", "SELECT * FROM rm.research_progress_plans WHERE policy_id=%s ORDER BY round", policy_id
            ),
            "observations": store.query(
                "human", "SELECT * FROM rm.research_progress_observations WHERE policy_id=%s ORDER BY recorded_at",
                policy_id,
            ),
            "lean_passes": store.query("human", "SELECT * FROM rm.lean_progress_history(%s,12)", policy_id),
        }


def _pass_summary(step):
    keys = ("round", "action", "status", "reason", "feedback", "focus", "lemma_id", "reduction_id",
            "model_attempt_id", "usage", "method", "route_checkpoint", "request_round",
            "model_provider", "workbench_focus_node_id", "workbench_reply")
    return {key: step[key][:1000] if isinstance(step[key], str) else step[key] for key in keys if key in step}


def _run_lean_policy(item: PendingLeanPass, *, lean, mathlib_project, lean_search_strategy=None, route_slots=1):
    from research_multiplier.workflows.lean_search import search

    with Store() as store, hold_lean_pass(store, item) as claimed:
        if claimed is None:
            return None
        started_at = monotonic_ns()
        rounds = item.model_rounds
        setting = claimed.settings()
        private = setting.private_model
        if private is not None:
            from research_multiplier.model_adapters import ModelConfig
            if ModelConfig.from_env("LEAN_AUTHOR", private=True).private_provenance() != private:
                raise ValueError("Use the exact private model configuration signed for this Lean policy.")
        continuation = claimed.continuation() if rounds else None
        strategy, author, route_event = lean_search_strategy or "ranked", None, None
        if lean_search_strategy is None and private is None:
            from research_multiplier.evaluations import lean_evolution
            strategy, author, route_event = lean_evolution.selection(store, needs_model=bool(rounds))
        options = {"author_config": author} if author is not None else {}
        if rounds:
            options["workbench_guidance_enabled"] = True
        route_width = setting.route_parallelism
        if route_width > 1:
            strategy = "ranked"
            options.update(parallel_routes=route_width, route_stall_limit=setting.route_stall_limit,
                           policy_id=item.policy_id, route_slots=min(route_width, route_slots))
        try:
            outcome = search(item.statement_id, rounds=rounds, confirm_public=bool(rounds) and private is None,
                             confirm_local=private is not None, lean=lean, mathlib_project=mathlib_project,
                             continue_work=claimed.is_active, continuation=continuation,
                             max_new_rounds=min(route_width, route_slots), strategy=strategy, **options)
        except VerificationQueueError as error:
            if error.retryable:
                # Leave the last completed checkpoint in place so the next pass
                # can reuse the existing model proposal and take a fresh lease.
                raise
            outcome = {"status": "verification_queue_blocked", "reason": str(error)}
            if continuation is not None:
                outcome["record"] = continuation
        report = {key: outcome[key] for key in ("status", "record", "evidence_id", "reason", "changes",
                                               "obligations", "reduction_id", "theorem_version_id",
                                               "native_attempts", "native_obligations",
                                               "method", "program_obligations", "open_routes",
                                               "assignments_checked", "finite_proof_attempt",
                                               "next_round", "target", "witness", "counterexample_attempt",
                                               "route_comparison", "route_attempts", "search_strategy", "parallel_batch", "route_lanes")
                  if key in outcome}
        if route_event is not None:
            report["lean_route_event_id"] = route_event
        report["worker_elapsed_ms"] = (monotonic_ns() - started_at) // 1_000_000
        if outcome.get("rounds"):
            report["rounds"] = [_pass_summary(step) for step in outcome["rounds"]]
        if len(json_text(report).encode("utf-8")) > 44_000:
            # The full trace stays in the object store. Keep PostgreSQL
            # progress observations small even for long propositions.
            for key in ("native_attempts", "native_obligations", "program_obligations", "open_routes",
                        "obligations", "counterexample_attempt", "rounds", "route_comparison", "route_attempts",
                        "parallel_batch", "route_lanes"):
                report.pop(key, None)
            report["details_in_record"] = True
        if report["status"] not in ("stopped", "awaiting_review_capacity"):
            claimed.finish(report)
        return {"policy_id": str(item.policy_id), **report}


def advance_cycle(stage, limit, policy_ids=None, parallel=2, *, lean=None, mathlib_project=None, lake_watch=None,
                  lean_search_strategy=None):
    if stage not in ("combined", "scheduler", "verifier"):
        raise ValueError("Choose combined, scheduler or verifier work.")
    if type(limit) is not int or not 1 <= limit <= 64:
        raise ValueError("Choose a cycle limit from 1 to 64.")
    if type(parallel) is not int or not 1 <= parallel <= 8:
        raise ValueError("Choose 1 to 8 local work slots.")
    scope = research_plans.worker_scope(policy_ids) if policy_ids is not None else None
    observations, started = 0, 0
    plans = []
    errors = []
    lean_runs = []
    review_paused = False
    with Store() as store:
        if stage in ("combined", "scheduler"):
            slots = store.query("scheduler", "SELECT rm.research_review_capacity() AS slots")[0]["slots"]
            work_limit = limit if slots is None else min(limit, slots)
            review_paused = work_limit == 0
            ready = store.query("scheduler", """
                SELECT id FROM rm.research_progress_status WHERE state IN ('ready', 'needs_plan')
                AND (%s::uuid[] IS NULL OR id = ANY(%s::uuid[]))
                ORDER BY (policy ->> 'priority')::integer DESC, approved_at, id LIMIT %s
            """, scope, scope, work_limit) if work_limit else []
            for item in ready:
                try:
                    plan, = store.query("scheduler", "SELECT rm.prepare_research_progress_plan(%s) AS id", item["id"])
                except ValueError as error:
                    errors.append({"policy_id": item["id"], "reason": str(error)})
                else:
                    if plan["id"] is not None:
                        plans.append(plan["id"])
            if plans:
                progress = research_plans.work_group(plans, work_limit, parallel)
                started = progress["attempts_started"]
                errors.extend(progress["worker_errors"])
            if stage == "combined" and started < work_limit:
                if lake_watch is not None:
                    lake_watch.observe(store, scope)
                lean_ready = pending_lean_passes(store, work_limit - started, scope)
                if lean_ready:
                    worker_count = min(parallel, len(lean_ready))
                    route_slots = max(1, parallel // worker_count)
                    with ThreadPoolExecutor(max_workers=worker_count) as workers:
                        tasks = []
                        for item in lean_ready:
                            project = (lake_watch.project_for(item.statement_id)
                                       if lake_watch is not None else mathlib_project)
                            task = workers.submit(copy_context().run, _run_lean_policy, item, lean=lean,
                                                  mathlib_project=project, lean_search_strategy=lean_search_strategy,
                                                  route_slots=route_slots)
                            tasks.append((item, task))
                        for item, task in tasks:
                            try:
                                result = task.result()
                            except DatabaseOperationError as error:
                                if error.retryable:
                                    raise
                                errors.append({"policy_id": item.policy_id, "reason": str(error)})
                            except (OSError, ValueError) as error:
                                errors.append({"policy_id": item.policy_id, "reason": str(error)})
                            else:
                                if result is not None:
                                    lean_runs.append(result)
                                    started += 1
        if stage in ("combined", "verifier"):
            ready = store.query("verifier", "SELECT * FROM rm.progress_verification_queue(%s,%s::uuid[])", limit, scope)
            for item in ready:
                try:
                    row, = store.query("verifier", "SELECT rm.record_research_progress(%s) AS observations", item["id"])
                except ValueError as error:
                    errors.append({"policy_id": item["id"], "reason": str(error)})
                else:
                    observations += row["observations"]
    return {"plans_visited": len(plans), "attempts_started": started, "observations": observations,
            "review_paused": review_paused, "errors": errors, "lean_runs": lean_runs}
