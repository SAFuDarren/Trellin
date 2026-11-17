"""Run bounded, counterbalanced Lean evaluation trajectories in restored twins."""

from decimal import Decimal
from pathlib import Path
import re
from time import monotonic
from uuid import UUID

from research_multiplier import evaluation
from research_multiplier.evaluations import lean_shadow, lean_shadow_build
from research_multiplier.storage import paths
from research_multiplier.storage.postgres import Store
from research_multiplier.protocol.serialization import fingerprint

_CASE_ID = re.compile(r"[a-z][a-z0-9-]{0,63}")
_EXPECTED = {"verified", "refuted", "open"}
_OUTCOMES = {"verified", "refuted", "no_result", "conflict"}
_CASE_FIELDS = {"id", "baseline_workspace", "candidate_workspace", "statements", "expected"}
_USAGE = ("input_tokens", "cached_input_tokens", "output_tokens", "execution_ms", "cost_usd")


def _manifest(document, relative_to):
    if (not isinstance(document, dict) or set(document) != {"format_version", "split", "cases"}
            or type(document["format_version"]) is not int or document["format_version"] != 1
            or document["split"] not in ("development", "holdout")
            or not isinstance(document["cases"], list) or not document["cases"]):
        raise ValueError("Use a version 1 Lean shadow manifest with a development or holdout split and at least one case.")
    cases, used, identifiers = [], set(), set()
    for entry in document["cases"]:
        if (not isinstance(entry, dict) or set(entry) != _CASE_FIELDS
                or not isinstance(entry["id"], str) or not _CASE_ID.fullmatch(entry["id"])
                or entry["id"] in identifiers or not isinstance(entry["statements"], list)
                or not isinstance(entry["expected"], list)
                or not 1 <= len(entry["statements"]) == len(entry["expected"])
                or any(not isinstance(outcome, str) or outcome not in _EXPECTED for outcome in entry["expected"])):
            raise ValueError("Each Lean shadow case needs a unique lowercase ID, ordered statement IDs and exact outcomes.")
        identifiers.add(entry["id"])
        try:
            statements = [str(UUID(value)) for value in entry["statements"]]
        except (AttributeError, TypeError, ValueError):
            raise ValueError("Use reviewed Lean statement UUIDs in the trajectory.") from None
        if set(statements) & used or len(set(statements)) != len(statements):
            raise ValueError("A Lean statement may occur only once in a campaign.")
        used.update(statements)
        workspaces = []
        for side in lean_shadow.SIDES:
            location = entry[f"{side}_workspace"]
            if not isinstance(location, str) or not location:
                raise ValueError("Each case needs two restored workspace paths.")
            path = Path(location).expanduser()
            workspaces.append(paths.workspace_details(relative_to / path))
        cases.append({"id": entry["id"], "statements": statements, "expected": entry["expected"],
                      "workspaces": workspaces})
    sealed = {"format_version": 1, "split": document["split"],
              "cases": [{key: case[key] for key in ("id", "statements", "expected")} for case in cases]}
    return cases, sealed


def _preflight(cases, source, identities, options, configs, comparison, remaining, *, builds, schema):
    prepared = []
    for index, case in enumerate(cases):
        environments = {side: lean_shadow._environment(side, configs[side], case_id=case["id"])
                        for side in lean_shadow.SIDES}
        steps = []
        for statement_id, expected in zip(case["statements"], case["expected"]):
            scope = lean_shadow._source(statement_id)
            requests = {
                side: lean_shadow._input(workspace, identity, scope, options, comparison[side], schema)
                for side, workspace, identity in zip(lean_shadow.SIDES, case["workspaces"],
                                                     identities[index * 2 + 1:index * 2 + 3])
            }
            states = {}
            for side in lean_shadow.SIDES:
                timeout = remaining()
                if not timeout:
                    raise ValueError("The aggregate Lean shadow time limit elapsed during paired preflight.")
                states[side] = lean_shadow._inspect(side, requests[side], environments[side],
                                                    comparison[side], timeout, build=builds.get(side))
            lean_shadow._same_start(states, source)
            steps.append({"statement_id": statement_id, "expected": expected, "scope": scope,
                          "requests": requests, "starting_states": states})
        order = list(lean_shadow.SIDES if index % 2 == 0 else reversed(lean_shadow.SIDES))
        prepared.append({"id": case["id"], "order": order, "environments": environments, "steps": steps})
    return prepared


def _outcome(expected):
    return "no_result" if expected == "open" else expected


def _summary(cases):
    totals = {}
    for side in lean_shadow.SIDES:
        runs = [(step["expected"], step["sides"][side]) for case in cases for step in case["steps"]]
        total = dict.fromkeys((*sorted(_OUTCOMES), "correct", "wrong", "errors", "reserved_calls",
                               "unmeasured_runs", "elapsed_seconds", "pending_scope_decisions",
                               "pending_evidence_reviews"), 0)
        summaries = []
        for expected, observed in runs:
            total["elapsed_seconds"] += observed.get("elapsed_seconds", 0)
            result = observed.get("result")
            if "error" in observed or result and result["search_status"] == "worker_error":
                total["errors"] += 1
            if result is None:
                total["unmeasured_runs"] += 1
                continue
            total["reserved_calls"] += result["model"]["reservation_count"]
            summaries.append(result["model"])
            for key in ("pending_scope_decisions", "pending_evidence_reviews"):
                total[key] += result["review"][key]
            outcome = result["outcome"]
            total[outcome] += 1
            if "error" not in observed and result["search_status"] != "worker_error":
                total["correct"] += outcome == _outcome(expected)
                total["wrong"] += outcome not in {"no_result", expected}
        total["elapsed_seconds"] = round(total["elapsed_seconds"], 3)
        for key in _USAGE:
            known = [summary[key]["reported_total"] for summary in summaries
                     if summary[key]["reported_total"] is not None]
            count = sum(summary[key]["reported_calls"] for summary in summaries)
            value = sum(map(Decimal, known)) if key == "cost_usd" else sum(known)
            total[key] = {"reported_total": (str(value) if key == "cost_usd" else value) if known else None,
                          "reported_calls": count,
                          "unknown_reported_usage": total["reserved_calls"] - count}
        totals[side] = total
    return totals


def _ablation_effect(cases):
    labels = {(True, True): "both_correct", (True, False): "enabled_only_correct",
              (False, True): "omitted_only_correct", (False, False): "neither_correct"}
    counts = dict.fromkeys((*labels.values(), "unmeasured_pairs"), 0)
    for case in cases:
        for step in case["steps"]:
            runs = [step["sides"][side] for side in lean_shadow.SIDES]
            if any("error" in run or run.get("result") is None or run["result"]["search_status"] == "worker_error"
                   for run in runs):
                counts["unmeasured_pairs"] += 1
                continue
            expected = _outcome(step["expected"])
            key = tuple(run["result"]["outcome"] == expected for run in runs)
            counts[labels[key]] += 1
    return counts


def _run(prepared, initial_corpus, comparison, remaining, builds):
    record = []
    for case in prepared:
        corpus = dict.fromkeys(lean_shadow.SIDES, initial_corpus)
        steps = []
        for index, step in enumerate(case["steps"]):
            order = case["order"] if not index % 2 else list(reversed(case["order"]))
            results = {}
            for side in order:
                request, environment = step["requests"][side], case["environments"][side]
                timeout = remaining()
                if not timeout or corpus[side] is None:
                    results[side] = {"error": "campaign_timeout" if not timeout else "previous_step_unavailable"}
                    continue
                start = step["starting_states"][side]
                try:
                    if index:
                        start = lean_shadow._inspect(side, request, environment, comparison[side], timeout, build=builds.get(side))
                        if start["corpus"] != corpus[side]:
                            raise ValueError("The reusable proof corpus changed outside its preceding trajectory step.")
                    timeout = remaining()
                    if not timeout:
                        results[side] = {"error": "campaign_timeout"}
                        continue
                    result = lean_shadow._measure(request, environment, start, comparison[side], timeout,
                                                  read_timeout=0, build=builds.get(side))
                except (OSError, ValueError) as error:
                    result = {"error": str(error)}
                results[side] = result
                outcome = result.get("result")
                corpus[side] = outcome.get("ending_corpus") if outcome is not None else None
            steps.append({"statement_id": step["statement_id"], "expected": step["expected"],
                          "scope": step["scope"], "order": order, "sides": results})
        record.append({"id": case["id"], "order": case["order"], "steps": steps})
    return record


def campaign(document, relative_to, profile=None, *, max_model_calls, campaign_timeout, corpus_sha256=None,
             builds=None, ablation=None, **settings):
    """Run cases with frozen expected outcomes and a checked aggregate spend envelope."""
    from . import lean_eval_corpus

    cases, manifest = _manifest(document, relative_to)
    sealed = None
    if corpus_sha256 is not None:
        with Store() as store:
            sealed = lean_eval_corpus.read(store, corpus_sha256, "verifier")
        lean_eval_corpus.require_strategy_coverage(sealed, settings["baseline_strategy"], settings["candidate_strategy"])
    if ablation is not None:
        if sealed is None:
            raise ValueError("A Lean component ablation needs a reviewed corpus seal.")
        if len(sealed["development"]) < lean_eval_corpus.MIN_ABLATION_CASES:
            raise ValueError("A Lean component ablation needs at least ten distinct reviewed development statements.")
    rounds = settings["rounds"]
    planned_steps = sum(len(case["statements"]) for case in cases)
    if (type(max_model_calls) is not int or max_model_calls < 0
            or type(rounds) is not int or rounds < 0
            or 2 * planned_steps * rounds > max_model_calls):
        raise ValueError("The planned paired model reservations exceed the aggregate campaign allowance.")
    if type(campaign_timeout) is not int or not 1 <= campaign_timeout <= 86_400:
        raise ValueError("Choose a finite campaign time limit between 1 and 86,400 seconds.")
    started = monotonic()

    def remaining():
        return max(0, min(settings["side_timeout"], campaign_timeout - (monotonic() - started)))

    builds = builds or {}
    options, configs, comparison, budget = lean_shadow._settings(profile, builds=builds, ablation=ablation, **settings)
    roots = [paths.require_workspace(), *(workspace for case in cases for workspace in case["workspaces"])]
    identities = lean_shadow._workspaces(roots)
    first = cases[0]["statements"][0]
    _, corpus = lean_shadow._source(first, corpus=True)
    schema = lean_shadow_build.database_revision(builds)
    identity = lean_shadow._report_identity(comparison, schema)
    if ablation is not None:
        identity.update(comparison_mode="component_ablation", activation_eligible=False)
    prepared = _preflight(cases, corpus, identities, options, configs, comparison, remaining, builds=builds, schema=schema)
    if sealed is not None:
        steps = [step for case in prepared for step in case["steps"]]
        lean_eval_corpus.verify_steps(sealed, manifest["split"], steps)
    record = _run(prepared, corpus, comparison, remaining, builds)
    totals = _summary(record)
    # Source changes are visible and therefore disqualify promotion even if child receipts are complete.
    try:
        _, final_corpus = lean_shadow._source(first, corpus=True)
        source_unchanged = final_corpus == corpus and (ablation is None or (
            lean_shadow.implementation_sha256() == identity["search_revision"]
        )) and (not builds or (
            lean_shadow_build.database_revision(builds) == schema
            and lean_shadow._report_identity(comparison, schema) == identity
        )) and all(
            lean_shadow._source(step["statement_id"]) == step["scope"]
            for case in prepared for step in case["steps"]
        )
    except (OSError, ValueError):
        source_unchanged = False
    report = {
        "format_version": 1, "kind": lean_shadow.CAMPAIGN_KIND, "split": manifest["split"],
        "manifest_sha256": fingerprint(manifest), "source": identities[0], "source_unchanged": source_unchanged,
        "starting_corpus": corpus, **identity,
        **({"evaluation_corpus_sha256": corpus_sha256} if sealed is not None else {}),
        "comparison": comparison,
        "budget": {**budget, "model_rounds_per_statement": rounds, "max_model_calls": max_model_calls,
                   "campaign_timeout_seconds": campaign_timeout, "planned_max_model_calls": 2 * planned_steps * rounds},
        "cases": record, "totals": totals, "campaign_wall_seconds": round(monotonic() - started, 3),
    }
    if ablation is not None:
        control = comparison["candidate"]["component_control"]
        report["ablation"] = {"name": control["name"], "version": control["version"], "omitted_side": "candidate",
                              "reviewed_development_cases": len(sealed["development"]),
                              "paired_outcomes": _ablation_effect(record)}
    return {**report, "report": evaluation.archive().put(report)}
