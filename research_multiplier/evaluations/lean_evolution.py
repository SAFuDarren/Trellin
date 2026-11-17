"""Researcher authorization for measured changes to public Lean advancement."""

from decimal import Decimal
import re

from research_multiplier import evaluation, model_adapters
from research_multiplier.evaluations import evolution
from research_multiplier.storage.postgres import Store
from research_multiplier.protocol.serialization import fingerprint

SIDES = ("baseline", "candidate")
CAMPAIGN = "lean-search-shadow-campaign"
_DEFAULT_STRATEGY = "ranked"
_APPROVED_STRATEGIES = {"legacy", "ranked"}
_DECISION_FIELDS = {
    "case_id", "baseline_minutes", "candidate_minutes", "baseline_invalid_receipts", "candidate_invalid_receipts",
}


def current(store, role="scheduler"):
    rows = store.query(role, "SELECT * FROM rm.lean_current_route")
    return rows[0] if rows else None


def _profile(profile):
    if profile is None:
        return model_adapters.ModelConfig.from_env("LEAN_AUTHOR", use_active=False)
    return evolution._profile_config(profile)


def _config(binding):
    from research_multiplier.evaluations import lean_shadow

    configured = _profile(binding["profile"])
    if (evolution.configuration_sha256(configured) != binding["configuration_sha256"]
            or fingerprint(lean_shadow.model_settings(configured)) != binding["effective_sha256"]):
        raise ValueError("Restore the evaluated Lean author settings before using this route.")
    return configured


def _implementation():
    from research_multiplier.evaluations import lean_shadow

    return lean_shadow.implementation_sha256()


def selection(store, *, needs_model, role="scheduler"):
    """Pin one live public route for the current worker pass."""
    event = current(store, role)
    if event is None:
        return _DEFAULT_STRATEGY, None, None
    binding = event["effective"]
    if binding["search_revision"] != _implementation():
        raise ValueError("The active Lean route belongs to another installed implementation. Restore it or record a human upgrade reset.")
    if event["action"] == "upgrade_reset":
        return _DEFAULT_STRATEGY, None, str(event["id"])
    return binding["strategy"], (lambda: _config(binding)) if needs_model else None, str(event["id"])


def _minutes(value):
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,6}(?:\.\d{1,3})?", value):
        raise ValueError("Record nonnegative review minutes per side, with at most three decimal places.")
    return Decimal(value)


def _coverage(step_result, expected):
    outcome = step_result.get("outcome")
    if outcome == "no_result":
        return False
    if outcome != expected or outcome not in {"verified", "refuted"}:
        raise ValueError("A paired holdout contains conflicting or unexpected exact evidence.")
    receipts = step_result.get("receipts")
    desired_kind = "lean" if outcome == "verified" else "counterexample"
    if not receipts or any(row.get("kind") != desired_kind or row.get("review_status") != "pending"
                           or not row.get("report", {}).get("sha256") for row in receipts):
        raise ValueError("The paired holdout lacks exact pending evidence for a reported outcome.")
    return True


def _gate(report, review):
    if report.get("kind") != CAMPAIGN or report.get("split") != "holdout" or report.get("source_unchanged") is not True:
        raise ValueError("Activation requires a paired Lean holdout campaign with an unchanged source.")
    if not isinstance(review, dict) or set(review) != {"format_version", "cases"} or review["format_version"] != 1:
        raise ValueError("Supply a version 1 Lean campaign review with a measurement for each case.")
    cases = report["cases"]
    expected_cases = {case["id"] for case in cases}
    reviews = review["cases"]
    if not isinstance(reviews, list) or any(not isinstance(row, dict) or set(row) != _DECISION_FIELDS for row in reviews):
        raise ValueError("Record minutes and invalid receipt counts for each case and side.")
    by_case = {row["case_id"]: row for row in reviews if isinstance(row["case_id"], str)}
    if len(cases) != len(expected_cases) or len(by_case) != len(reviews) or set(by_case) != expected_cases:
        raise ValueError("Review each immutable paired holdout case exactly once.")
    if {tuple(case["order"]) for case in cases} != {SIDES, SIDES[::-1]}:
        raise ValueError("A Lean activation comparison needs each strategy to run first on a holdout case.")

    totals = {side: {"exact": 0, "calls": 0, "review_minutes": Decimal()} for side in SIDES}
    targets = set()
    labels = set()
    for case in cases:
        measured = by_case[case["id"]]
        for side in SIDES:
            invalid = measured[f"{side}_invalid_receipts"]
            if type(invalid) is not int or invalid < 0:
                raise ValueError("Record the number of invalid receipts as a nonnegative integer.")
            if invalid:
                raise ValueError("Resolve every independently found receipt error before activating a Lean route.")
            totals[side]["review_minutes"] += _minutes(measured[f"{side}_minutes"])
        if not case["steps"]:
            raise ValueError("Every paired holdout case must contain at least one reviewed statement.")
        for step in case["steps"]:
            target = step["statement_id"]
            expected = step["expected"]
            if target in targets or expected not in {"verified", "refuted", "open"}:
                raise ValueError("The holdout needs distinct frozen statements with explicit expected outcomes.")
            targets.add(target)
            labels.add(expected)
            exact = {}
            for side in SIDES:
                run = step["sides"][side]
                result = run.get("result")
                if run.get("error") or not isinstance(result, dict) or result.get("search_status") in {
                    "worker_error", "timeout", "proposal_failed", "idle",
                }:
                    raise ValueError("Repair incomplete or failed holdout runs before activating a Lean route.")
                exact[side] = _coverage(result, expected)
                totals[side]["exact"] += exact[side]
                calls = result["model"]["reservation_count"]
                if type(calls) is not int or calls < 0:
                    raise ValueError("The holdout needs a complete reservation count for each run.")
                totals[side]["calls"] += calls
            if exact["baseline"] and not exact["candidate"]:
                raise ValueError("The challenger lost an exact baseline result.")

    if not {"verified", "refuted"} <= labels:
        raise ValueError("Activation requires both provable and refutable holdout statements.")
    before, after = (totals[side] for side in SIDES)
    if after["review_minutes"] > before["review_minutes"]:
        raise ValueError("The challenger increased measured research review time.")
    if not after["exact"] or not (after["exact"] > before["exact"] or (
        after["calls"] <= before["calls"] and
        (after["calls"] < before["calls"] or after["review_minutes"] < before["review_minutes"])
    )):
        raise ValueError("The challenger needs more exact results or a measured efficiency improvement on the holdout.")
    return {side: {**item, "review_minutes": str(item["review_minutes"])} for side, item in totals.items()}


def _binding(report, side):
    compared = report["comparison"][side]
    setting = {name: compared[name] for name in ("strategy", "profile", "configuration_sha256")}
    if setting["strategy"] not in _APPROVED_STRATEGIES:
        raise ValueError("The compared Lean strategy is unavailable in this implementation.")
    if not setting["configuration_sha256"]:
        raise ValueError("Activation needs a compared model configuration. A zero-model campaign provides program-only evidence.")
    if compared["implementation_sha256"] != report["search_revision"] or not compared["effective_settings"]:
        raise ValueError("The compared adapter or implementation is missing its verified identity.")
    return {**setting, "search_revision": report["search_revision"],
            "effective_sha256": fingerprint(compared["effective_settings"])}


def activate(checksum, review, actor, reason):
    from . import lean_eval_corpus
    from research_multiplier.evaluations import lean_shadow

    report = lean_shadow.status(checksum)
    if report.get("comparison_mode") == "installed_builds":
        raise ValueError("A cross-build Lean report is diagnostic. Review the software upgrade separately before activating a route.")
    if report.get("comparison_mode") == "component_ablation":
        raise ValueError("A Lean component ablation is diagnostic. Review the software change separately before activating a route.")
    if report.get("search_revision") != _implementation():
        raise ValueError("Activate from the exact Lean implementation used for the paired campaign.")
    gate = _gate(report, review)
    with Store() as store:
        sealed = lean_eval_corpus.read(store, report.get("evaluation_corpus_sha256"), "verifier")
    steps = [step for case in report["cases"] for step in case["steps"]]
    lean_eval_corpus.verify_steps(sealed, "holdout", steps)
    baseline, candidate = (_binding(report, side) for side in SIDES)
    lean_eval_corpus.require_strategy_coverage(sealed, baseline["strategy"], candidate["strategy"])
    if baseline == candidate:
        raise ValueError("The paired campaign did not compare two distinct effective settings.")
    for entry in (baseline, candidate):
        _config(entry)
    if baseline["strategy"] == candidate["strategy"] and baseline["configuration_sha256"] == candidate["configuration_sha256"]:
        raise ValueError("The Lean profiles resolve to the same effective settings.")
    archive = evaluation.archive()
    hashes = set()
    for case in report["cases"]:
        for step in case["steps"]:
            for side in SIDES:
                run = step["sides"][side]
                refs = {receipt[key]["sha256"] for receipt in run["result"]["receipts"] for key in ("report", "candidate")}
                retained = set(run.get("receipt_artifacts", ()))
                if not refs <= retained:
                    raise ValueError("A paired holdout is missing archived verification artifacts.")
                hashes.update(retained)
    for digest in sorted(hashes):
        archive.read({"uri": f"urn:sha256:{digest}", "sha256": digest})
    reviewed = archive.put({"format_version": 1, "campaign_sha256": checksum, "review": review, "measurements": gate})
    with Store() as store:
        event = current(store, "human")
        if ((event is None or event["action"] == "upgrade_reset")
                and (baseline["profile"] is not None or baseline["strategy"] != _DEFAULT_STRATEGY)):
            raise ValueError("Compare the challenger against the current default Lean route before activation.")
        attestation = (event["id"] if event else None, checksum, reviewed["sha256"],
                       store.json(baseline), store.json(candidate), store.json(gate))
        store.query("verifier", "SELECT rm.attest_lean_route(%s,%s,%s,%s,%s,%s)", *attestation)
        [saved] = store.query("human", "SELECT rm.activate_lean_route(%s,%s,%s,%s,%s,%s,%s,%s) AS event_id",
                              *attestation, actor, reason)
    return {**saved, "effective": candidate, "review": reviewed, "measurements": gate}


def rollback(event_id, actor, reason):
    with Store() as store:
        rows = store.query("human", """
            SELECT event.baseline FROM rm.lean_route_events event JOIN rm.lean_current_route active USING (id)
            WHERE event.id = %s AND event.action = 'activate'
        """, event_id)
        if not rows:
            raise ValueError("Rollback requires the current Lean activation event.")
        [previous] = rows
        binding = previous["baseline"]
        if binding["search_revision"] != _implementation():
            raise ValueError("Restore the installed Lean implementation recorded by the previous route first.")
        _config(binding)
        [saved] = store.query("human", "SELECT rm.rollback_lean_route(%s,%s,%s) AS event_id", event_id, actor, reason)
    return {**saved, "effective": binding}


def reset_after_upgrade(event_id, actor, reason):
    """Retire an old implementation's active route and resume the installed default."""
    with Store() as store:
        [saved] = store.query("human", "SELECT rm.reset_lean_route_after_upgrade(%s,%s,%s,%s) AS event_id",
                              event_id, _implementation(), actor, reason)
    return {**saved, "action": "upgrade_reset"}


def status():
    with Store() as store:
        event = current(store, "human")
        observations = {} if event is None else {
            row["status"]: row["passes"] for row in store.query(
                "human", "SELECT * FROM rm.lean_route_observations(%s)", event["id"])
        }
        events = store.query("human", """
            SELECT id, action, previous_event_id, campaign_sha256, review_sha256, recorded_at, effective,
                   measurements, actor_ref, reason FROM rm.lean_route_events ORDER BY sequence_no DESC LIMIT 20
        """)
    implementation_current = event is None or event["effective"]["search_revision"] == _implementation()
    return {"active": event, "events": events, "implementation_current": implementation_current,
            "worker_passes_last_7_days": observations}
