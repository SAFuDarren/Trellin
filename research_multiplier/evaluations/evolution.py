"""Paired production-prover trials, scoped to private evaluation records."""

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import fields
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from time import monotonic

from research_multiplier import eval_component, eval_injections, evaluation, flow_certificate, flow_claim
from research_multiplier.workflows import formalization
from research_multiplier import model_adapters, production, verification, v0a_models
from research_multiplier.storage import paths
from research_multiplier.domain_packages import production_domains
from research_multiplier.models.model_errors import ModelFailure, ModelOutputError
from research_multiplier.storage.postgres import Store
from research_multiplier.protocol.serialization import fingerprint

ROLE = "PROVER"
ROUTE = "production-prover-shadow"
PROFILE = re.compile(r"[A-Z][A-Z0-9_]{0,31}")


def _source(module):
    return sha256(Path(module.__file__).read_bytes()).hexdigest()


def configuration_sha256(config):
    """Bind exact non-secret profile settings without making an update invalidate the route."""
    parameters = {key.name: getattr(config, key.name) for key in fields(config) if key.name != "api_key"}
    return fingerprint({"config": parameters, "route": ROLE})


def production_revision():
    sources = (model_adapters, v0a_models, production, flow_certificate, verification)
    return fingerprint({module.__name__: _source(module) for module in sources})


def _profile_config(profile):
    if not isinstance(profile, str) or not PROFILE.fullmatch(profile):
        raise ValueError("Choose a model profile name with up to 32 uppercase letters, underscores or digits.")
    return model_adapters.ModelConfig.from_env(
        ROLE, use_active=False, config_prefix=f"RESEARCH_MULTIPLIER_PROFILE_{profile}_",
    )


def _current(store, role):
    rows = store.query(role, "SELECT id, profile, configuration_sha256 FROM rm.shadow_current_routes WHERE route=%s",
                       ROLE)
    return rows[0] if rows else None


def _selected(default, route):
    if route is None:
        return default
    config = _profile_config(route["profile"]) if route["profile"] else default
    if configuration_sha256(config) != route["configuration_sha256"]:
        raise ValueError("The selected production model profile changed after approval. Restore its approved settings.")
    return config


def select_model_config(role, default):
    """Resolve an approved configuration only for a bound production workspace."""
    if role.upper() not in (ROLE, "PROOF"):
        return default
    workspace = paths.active_workspace()
    if workspace is None or paths.database_id(workspace) is None:
        return default
    login = next((item for item in ("verifier", "human", "scheduler")
                  if os.environ.get(f"RM_{item.upper()}_DSN")), None)
    if login is None:
        return default
    with Store() as store:
        return _selected(default, _current(store, login))


def _probe(case):
    group, domain, frozen, subject = case
    witness = eval_injections._checked_candidate(domain, frozen)
    if group == "scope_drift":
        return eval_injections._change_scope(domain, frozen)
    return eval_injections._damage_witness(domain, frozen, witness, subject)


def _proof_response(config, auditor, case, requirements, artifacts, checker, timeout, screened):
    _, domain, frozen, subject = case
    if screened:
        result = domain.candidate(frozen["small_case_spec"])
        return {"action": "submit", "subject_sha256": subject, "certificate": result["certificate"]}, {
            "path": "deterministic_refutation",
        }
    try:
        result = v0a_models.prove(config, frozen, requirements, timeout=timeout)
    except ModelOutputError as error:
        return {"action": "abstain"}, {"failure": error.as_dict(), "model": error.metadata,
                                       "transcript": artifacts.put(error.transcript)}
    except ModelFailure as error:
        if error.code in {"configuration", "project_budget_exhausted"}:
            raise
        return {"action": "abstain"}, {"failure": error.as_dict()}
    details = {"model": result.metadata, "transcript": artifacts.put(result.transcript)}
    candidate = result.output
    if "failure_reason" in candidate or "clarification" in candidate:
        return {"action": "abstain"}, details
    # Use the model production checker and archive the proposed certificate
    # independently. The comparison can always replay rejected witnesses.
    try:
        checked = verification.check(frozen, candidate, checker)
    except ValueError:
        details["verification"] = "certificate_check_failed"
        return {"action": "submit", "subject_sha256": subject, "certificate": candidate["certificate"]}, details
    details["verification"] = artifacts.put(checked)
    details["audit_attempted"] = True
    try:
        review = v0a_models.audit(auditor, frozen, candidate, requirements, timeout=timeout)
    except ModelOutputError as error:
        details["audit"] = {"failure": error.as_dict(), "model": error.metadata,
                            "transcript": artifacts.put(error.transcript)}
        return {"action": "abstain"}, details
    except ModelFailure as error:
        if error.code in {"configuration", "project_budget_exhausted"}:
            raise
        details["audit"] = {"failure": error.as_dict()}
        return {"action": "abstain"}, details
    details["audit"] = {"model": review.metadata, "transcript": artifacts.put(review.transcript),
                        "verdict": review.output["verdict"], "diff_status": review.output["diff_status"]}
    if review.output["diff_status"] == "different":
        return {"action": "reject"}, details
    if (review.output["verdict"], review.output["diff_status"]) != ("verified", "exact"):
        return {"action": "abstain"}, details
    return {"action": "submit", "subject_sha256": subject, "certificate": candidate["certificate"]}, details


def _metrics(attempts):
    responses = [record for item in attempts.values() for record in (item, item.get("audit", {}))]
    models = [item["model"] for item in responses if "model" in item]
    costs = [Decimal(str(model["cost_usd"])) for model in models if model.get("cost_usd") is not None]
    return {
        "elapsed_ms": sum(item["elapsed_ms"] for item in attempts.values()),
        "model_calls": sum((item["path"] == "model") + item.get("audit_attempted", False) for item in attempts.values()),
        "cost_usd_reported": str(sum(costs)) if costs else None,
        "cost_reported_calls": len(costs),
        "audit_scope_drifts": sum(item.get("audit", {}).get("diff_status") == "different"
                                  for item in attempts.values()),
    }


def _configs(profile):
    with Store() as store:
        route = _current(store, "verifier")
        baseline = _selected(model_adapters.ModelConfig.from_env(ROLE, use_active=False), route)
    candidate = _profile_config(profile)
    auditor = model_adapters.ModelConfig.from_env("AUDITOR", use_active=False)
    v0a_models.validate_pair(baseline, auditor)
    v0a_models.validate_pair(candidate, auditor)
    sides = {"baseline": baseline, "candidate": candidate}
    digests = {name: configuration_sha256(config) for name, config in sides.items()}
    if len(set(digests.values())) != 2:
        raise ValueError("The candidate profile must differ from the current production configuration.")
    return route, sides, auditor, digests


def _attempt(config, auditor, identifier, case, requirements, contract, checker, timeout, probe,
             screened, live_input, artifacts):
    if live_input is not None:
        with Store() as store:
            if _live_input(store, identifier) != live_input:
                raise ValueError("The live research context changed during the paired trial.")
    started = monotonic()
    if probe is not None:
        response, detail = probe, {"path": "injected_checker_probe"}
        evaluation._judge(case, response)
    elif live_input is not None and screened and "formalization" not in requirements:
        response, detail = {"action": "abstain"}, {"path": "screen_requires_formalization"}
    else:
        response, detail = _proof_response(config, auditor, case, requirements, artifacts, checker, timeout, screened)
    return response, {
        "path": "model", **detail, "contract": contract, "response": artifacts.put(response),
        "elapsed_ms": round((monotonic() - started) * 1000),
    }


def _pair(cases, sides, auditor, timeout, *, live_inputs=None):
    artifacts = evaluation.archive()
    checker = verification.checker_identity(production_domains.DEFAULT_DOMAIN)
    responses = {name: {} for name in sides}
    attempts = {name: {} for name in sides}
    with ThreadPoolExecutor(max_workers=len(sides)) as pool:
        for identifier, case in sorted(cases.items()):
            group, _, frozen, subject = case
            live_input = live_inputs[identifier] if live_inputs is not None else None
            requirements = live_input["proof_requirements"] if live_input is not None else flow_claim.CERTIFICATE_REQUIREMENTS
            contract = artifacts.put({"frozen_claim": frozen, "subject_sha256": subject, "proof_requirements": requirements})
            probe = _probe(case) if group in ("scope_drift", "invalid_proof") else None
            screened = probe is None and flow_certificate.check_small_cases(frozen["small_case_spec"])["counterexample"]
            pending = {name: pool.submit(copy_context().run, _attempt, config, auditor, identifier, case, requirements,
                                         contract, checker, timeout, probe, screened, live_input, artifacts)
                       for name, config in sides.items()}
            for name, future in pending.items():
                responses[name][identifier], attempts[name][identifier] = future.result()
    return responses, attempts


def shadow(development, holdout, profile, *, confirm_public=False, max_cases=32, timeout_seconds=production.MODEL_TIMEOUT):
    """Execute both responders against the same frozen holdout before evaluating."""
    if (not confirm_public or type(max_cases) is not int or not 1 <= max_cases <= 128
            or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= production.MODEL_TIMEOUT):
        raise ValueError("Confirm public inputs and supply valid limits for shadow proof evaluation.")
    cases = evaluation._split(development, holdout)["holdout"]
    if len(cases) > max_cases or any(case[1].name != production_domains.DEFAULT_DOMAIN for case in cases.values()):
        raise ValueError("The production prover trial requires a bounded fixed-network maximum-flow holdout.")
    if any(len(identifier) > 180 for identifier in cases):
        raise ValueError("Each shadow case ID must be at most 180 characters.")

    route, sides, auditor, digests = _configs(profile)
    artifacts = evaluation.archive()
    responses, attempts = _pair(cases, sides, auditor, timeout_seconds)
    documents = {
        name: {"format_version": 1, "version": {"harness": ROUTE, "model": config.model, "revision": digests[name]},
               "responses": responses[name]}
        for name, config in sides.items()
    }
    body, reference = evaluation.compare(development, holdout, documents["baseline"], documents["candidate"], "holdout")
    result = evaluation.summary(body, reference)
    manifest = {
        "format_version": 1, "mode": ROUTE, "comparison": reference, "production_revision": production_revision(),
        "baseline_event_id": str(route["id"]) if route else None,
        "baseline_sha256": digests["baseline"], "candidate_profile": profile, "candidate_sha256": digests["candidate"],
        "auditor_sha256": configuration_sha256(auditor),
        "case_ids": {identifier: case[3] for identifier, case in cases.items()},
        "limits": {"max_cases": max_cases, "timeout_seconds": timeout_seconds},
        "attempts": attempts, "metrics": {name: _metrics(attempts[name]) for name in sides},
        "paired_outcomes": eval_component._paired(body),
    }
    experiment = artifacts.put(manifest)
    with Store() as store:
        def register():
            [saved_report] = store.query("verifier", "SELECT rm.register_eval_report(%s) AS id", store.json(result))
            return saved_report["id"], store.query("verifier",
                "SELECT rm.register_shadow_experiment(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) AS id",
                experiment["sha256"], saved_report["id"], manifest["baseline_event_id"], digests["baseline"],
                profile, digests["candidate"], manifest["auditor_sha256"], store.json(manifest["case_ids"]),
                manifest["metrics"]["baseline"]["audit_scope_drifts"],
                manifest["metrics"]["candidate"]["audit_scope_drifts"],
            )[0]["id"]

        report_id, experiment_id = store.transaction("verifier", register)
    return {
        "experiment_id": experiment_id, "experiment": experiment, "evaluation_report_id": report_id,
        "evaluation_report": reference, "recommended": result["recommended"], "metrics": manifest["metrics"],
        "paired_outcomes": manifest["paired_outcomes"],
    }


def _live_input(store, identifier):
    row = store.frozen(identifier)
    if row is None or row["status"] not in ("open", "proved", "refuted"):
        raise ValueError("Live shadow requires a current frozen claim.")
    frozen = json.loads(row["contract_bytes"])
    if (frozen["sensitivity"] != "public" or frozen["domain"] != production_domains.DEFAULT_DOMAIN
            or frozen["claim_id"] != str(identifier)
            or sha256(row["contract_bytes"].encode("utf-8")).hexdigest() != row["contract_sha256"]):
        raise ValueError("Live shadow only accepts exact frozen public fixed-network claims.")
    domain = production_domains.get(frozen["domain"])
    domain.validate_claim(frozen)
    status = formalization.read(store, "claim_id", identifier)
    packet = formalization.proof_packet(status, frozen) if status else None
    if packet:
        store.query("verifier", "SELECT rm.require_public_model_packet(%s)", packet["id"])
    known = None
    if row["status"] in ("proved", "refuted") and row["decisive_evidence_id"]:
        receipts = store.query("verifier", """
            SELECT kind FROM rm.evidence_foundation_status
            WHERE id=%s AND NOT needs_recheck AND verdict='verified' AND diff_status='exact' AND review_status='approved'
        """, row["decisive_evidence_id"])
        if receipts:
            known = "verified" if row["status"] == "proved" else "refuted"
    return {
        "frozen_claim": frozen, "subject_sha256": row["contract_sha256"],
        "proof_requirements": production.model_requirements(store, status, packet),
        "reviewed_outcome": known,
    }


def _judge_live(cases, responses, attempts, expected):
    outcomes = {
        side: {identifier: evaluation._judge(case, responses[side][identifier])["observed"]
               for identifier, case in cases.items()}
        for side in ("baseline", "candidate")
    }
    metrics = {side: _metrics(attempts[side]) for side in outcomes}
    counts = {
        "cases": len(cases),
        "reviewed_cases": sum(value is not None for value in expected.values()),
        **{f"{side}_conclusive": sum(outcome in evaluation.CONCLUSIVE for outcome in observed.values())
           for side, observed in outcomes.items()},
        "candidate_reviewed_conflicts": sum(
            expected[identifier] is not None and outcome in evaluation.CONCLUSIVE and expected[identifier] != outcome
            for identifier, outcome in outcomes["candidate"].items()
        ),
        **{f"{side}_audit_scope_drifts": item["audit_scope_drifts"] for side, item in metrics.items()},
    }
    paired = {
        identifier: {"reviewed_outcome": expected[identifier],
                     **{side: observed[identifier] for side, observed in outcomes.items()}}
        for identifier in cases
    }
    return counts, paired, metrics


def _live_cases(inputs):
    return {
        identifier: ("known_theorem", production_domains.get(item["frozen_claim"]["domain"]),
                     item["frozen_claim"], item["subject_sha256"])
        for identifier, item in inputs.items()
    }


def shadow_live(claim_ids, profile, *, confirm_public=False):
    identifiers = [str(item) for item in claim_ids]
    if not confirm_public or not 1 <= len(identifiers) <= 32 or len(set(identifiers)) != len(identifiers):
        raise ValueError("Confirm public use and select 1 to 32 distinct frozen production claims.")
    route, sides, auditor, digests = _configs(profile)
    with Store() as store:
        inputs = {identifier: _live_input(store, identifier) for identifier in identifiers}
    cases = _live_cases(inputs)
    responses, attempts = _pair(cases, sides, auditor, production.MODEL_TIMEOUT, live_inputs=inputs)
    expected = {identifier: item["reviewed_outcome"] for identifier, item in inputs.items()}
    counts, paired, metrics = _judge_live(cases, responses, attempts, expected)
    manifest = {
        "mode": "production-live-shadow", "format_version": 1, "production_revision": production_revision(),
        "attempts": attempts, "counts": counts,
        "paired_outcomes": paired, "metrics": metrics,
        "baseline_event_id": str(route["id"]) if route else None,
        "baseline_sha256": digests["baseline"], "candidate_sha256": digests["candidate"],
        "auditor_sha256": configuration_sha256(auditor),
    }
    trial = evaluation.archive().put(manifest)
    with Store() as store:
        for identifier, original in inputs.items():
            if _live_input(store, identifier) != original:
                raise ValueError("The live research context changed before the paired trial could be recorded.")
        [entry] = store.query("verifier", "SELECT rm.register_shadow_live_trial(%s,%s,%s,%s,%s,%s) AS id",
                               trial["sha256"], manifest["baseline_event_id"], digests["baseline"],
                               digests["candidate"], manifest["auditor_sha256"], store.json(counts))
    return {"trial_id": entry["id"], "trial": trial, "counts": counts, "metrics": metrics,
            "paired_outcomes": paired}


def _live_record(store, trial_id, digest=None):
    rows = store.query("human", "SELECT * FROM rm.shadow_live_trials WHERE id=%s", trial_id)
    if not rows:
        raise ValueError("No live paired shadow trial matches this ID.")
    [entry] = rows
    checksum = entry["trial_sha256"]
    if digest is not None and checksum != digest:
        raise ValueError("Confirm the exact live shadow trial hash.")
    artifacts = evaluation.archive()
    manifest = artifacts.read_json({"uri": f"urn:sha256:{checksum}", "sha256": checksum})
    if (manifest.get("mode") != "production-live-shadow"
            or any(manifest.get(key) != entry[key] for key in (
                "baseline_sha256", "candidate_sha256", "auditor_sha256", "counts",
            )) or manifest.get("baseline_event_id") != (
                str(entry["baseline_event_id"]) if entry["baseline_event_id"] else None
            )):
        raise ValueError("The live trial artifact differs from its registered version.")
    if digest is not None:
        attempts = manifest["attempts"]
        inputs = {
            identifier: artifacts.read_json(item["contract"])
            for identifier, item in attempts["baseline"].items()
        }
        if set(inputs) != attempts["candidate"].keys() or any(
            artifacts.read_json(item["contract"]) != inputs[identifier]
            for identifier, item in attempts["candidate"].items()
        ):
            raise ValueError("Live trial sides must use the same exact proof context.")
        responses = {side: {identifier: artifacts.read_json(item["response"]) for identifier, item in items.items()}
                     for side, items in attempts.items()}
        expected = {identifier: value["reviewed_outcome"] for identifier, value in manifest["paired_outcomes"].items()}
        if _judge_live(_live_cases(inputs), responses, attempts, expected) != (
            entry["counts"], manifest["paired_outcomes"], manifest["metrics"],
        ):
            raise ValueError("The live trial differs from independently replayed outcomes.")
    return entry, manifest


def live_status(trial_id):
    with Store() as store:
        entry, manifest = _live_record(store, trial_id)
        return {"trial": entry, "metrics": manifest["metrics"], "paired_outcomes": manifest["paired_outcomes"]}


def _replay_experiment(manifest, summary, artifacts):
    body, report = evaluation.reproduce(manifest["comparison"]["sha256"])
    if evaluation.summary(body, report) != summary or eval_component._paired(body) != manifest["paired_outcomes"]:
        raise ValueError("The reviewed comparison differs from the reproduced shadow evaluation.")
    cases = evaluation._cases(artifacts.read_json(body["inputs"]["holdout"]))
    for side in ("baseline", "candidate"):
        response = artifacts.read_json(body["inputs"][side])["responses"]
        attempts = manifest["attempts"][side]
        if attempts.keys() != cases.keys() or _metrics(attempts) != manifest["metrics"][side]:
            raise ValueError("The shadow attempt archive differs from the paired evaluation.")
        for case_id, (_, _, frozen, subject) in cases.items():
            item = attempts[case_id]
            if (artifacts.read_json(item["contract"]) != {
                    "frozen_claim": frozen, "subject_sha256": subject,
                    "proof_requirements": flow_claim.CERTIFICATE_REQUIREMENTS,
                }
                    or artifacts.read_json(item["response"]) != response[case_id]):
                raise ValueError("A paired shadow attempt differs from its frozen contract or response.")


def _reviewed_record(store, experiment_id, *, digest=None):
    [entry] = store.query("human", """
        SELECT e.*, r.summary, r.approved_at, t.measured_cases, t.paired_cases, t.baseline_minutes, t.candidate_minutes
        FROM rm.shadow_experiments e JOIN rm.evaluation_report_status r ON r.id=e.report_id
        JOIN rm.shadow_review_summary t ON t.experiment_id=e.id
        WHERE e.id=%s
    """, experiment_id) or [None]
    if entry is None:
        raise ValueError("No paired shadow experiment matches this ID.")
    checksum = entry["experiment_sha256"]
    if digest is not None and checksum != digest:
        raise ValueError("Confirm the exact shadow experiment hash.")
    reference = {"uri": f"urn:sha256:{checksum}", "sha256": checksum}
    artifacts = evaluation.archive()
    manifest = artifacts.read_json(reference)
    if (manifest.get("mode") != ROUTE or manifest.get("comparison") != entry["summary"]["artifact"]
            or any(manifest["metrics"][side]["audit_scope_drifts"] != entry[f"{side}_audit_scope_drifts"]
                   for side in ("baseline", "candidate"))
            or any(manifest.get(key) != entry[key] for key in (
                "baseline_sha256", "candidate_profile", "candidate_sha256", "auditor_sha256", "case_ids",
            )) or manifest.get("baseline_event_id") != (
                str(entry["baseline_event_id"]) if entry["baseline_event_id"] else None
            )):
        raise ValueError("The experiment archive differs from its recorded binding.")
    if digest is not None:
        _replay_experiment(manifest, entry["summary"], artifacts)
    return entry, manifest


def status(experiment_id):
    with Store() as store:
        entry, manifest = _reviewed_record(store, experiment_id)
        events = store.query("human", """
            SELECT id, action, live_trial_id, previous_event_id, profile, configuration_sha256, actor_ref, reason, recorded_at
            FROM rm.shadow_route_events WHERE experiment_id=%s ORDER BY sequence_no
        """, experiment_id)
        entry["events"] = events
        entry["active_route"] = _current(store, "human")
        return {"experiment": entry, "metrics": manifest["metrics"], "paired_outcomes": manifest["paired_outcomes"]}


def record_review(experiment_id, case_id, side, minutes, actor):
    with Store() as store:
        store.query("human", "SELECT rm.record_shadow_review(%s,%s,%s,%s,%s)",
                    experiment_id, case_id, side, minutes, actor)
    return {"experiment_id": experiment_id, "case_id": case_id, "side": side, "minutes": minutes}


def activate(experiment_id, digest, live_trial, live_digest, actor, reason):
    with Store() as store:
        entry, manifest = _reviewed_record(store, experiment_id, digest=digest)
        trial, live_manifest = _live_record(store, live_trial, digest=live_digest)
        if manifest["production_revision"] != production_revision() or (
            live_manifest["production_revision"] != manifest["production_revision"]
        ):
            raise ValueError("Activate using the production implementation tested in both trial records.")
        if trial["auditor_sha256"] != manifest["auditor_sha256"]:
            raise ValueError("The holdout and live trial must use the same independent auditor.")
        candidate = _profile_config(entry["candidate_profile"])
        current = _current(store, "human")
        default = model_adapters.ModelConfig.from_env(ROLE, use_active=False)
        if (configuration_sha256(candidate) != entry["candidate_sha256"]
                or configuration_sha256(_selected(default, current)) != entry["baseline_sha256"]):
            raise ValueError("Production model settings changed since the paired experiment.")
        auditor = model_adapters.ModelConfig.from_env("AUDITOR", use_active=False)
        if manifest["auditor_sha256"] != configuration_sha256(auditor):
            raise ValueError("The independent auditor changed since the paired experiment.")
        if manifest["limits"]["timeout_seconds"] != production.MODEL_TIMEOUT:
            raise ValueError("Only a paired experiment at the current production timeout can activate a route.")
        v0a_models.validate_pair(candidate, auditor)
        [result] = store.query("human", "SELECT rm.activate_shadow_route(%s,%s,%s,%s,%s,%s) AS route_event_id",
                               experiment_id, digest, live_trial, live_digest, actor, reason)
        return result


def rollback(event_id, actor, reason):
    with Store() as store:
        rows = store.query("human", """
            SELECT prior.profile, study.baseline_sha256
            FROM rm.shadow_route_events activation
            JOIN rm.shadow_current_routes current ON current.id = activation.id
            JOIN rm.shadow_experiments study ON study.id = activation.experiment_id
            LEFT JOIN rm.shadow_route_events prior ON prior.id = activation.previous_event_id
            WHERE activation.id=%s AND activation.action='activate'
        """, event_id)
        if not rows:
            raise ValueError("Rollback requires the current activation event.")
        [previous] = rows
        config = (_profile_config(previous["profile"]) if previous["profile"]
                  else model_adapters.ModelConfig.from_env(ROLE, use_active=False))
        if configuration_sha256(config) != previous["baseline_sha256"]:
            raise ValueError("Restore the previous profile's exact configuration before completing rollback.")
        [result] = store.query("human", "SELECT rm.rollback_shadow_route(%s,%s,%s) AS route_event_id",
                               event_id, actor, reason)
        return result
