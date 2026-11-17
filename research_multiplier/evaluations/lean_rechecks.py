"""Collect local evaluation signals and execute queued development replays."""

from datetime import datetime, timezone

from .. import evaluation
from . import lean_eval_corpus, lean_incident_regression
from research_multiplier.evaluations import lean_shadow
from research_multiplier.storage.postgres import DatabaseOperationError, Store
from research_multiplier.protocol.serialization import fingerprint


def _benchmark(store, checksum, implementation):
    report = lean_shadow.status(checksum)
    if (report.get("kind") != lean_shadow.CAMPAIGN_KIND or report.get("split") != "development"
            or report.get("source_unchanged") is not True):
        raise ValueError("Observe a complete Lean campaign on an unchanged sealed development corpus.")
    corpus_sha = report.get("evaluation_corpus_sha256")
    corpus = lean_eval_corpus.read(store, corpus_sha, "verifier")
    try:
        steps = [step for case in report["cases"] for step in case["steps"]]
        lean_eval_corpus.verify_steps(corpus, "development", steps)
        for step in steps:
            for side in lean_shadow.SIDES:
                run = step["sides"][side]
                outcome = run.get("result") or {}
                if run.get("error") or outcome.get("search_status") in {
                    None, "worker_error", "timeout", "proposal_failed", "idle",
                } or outcome.get("outcome") not in {
                    "no_result", step["expected"],
                }:
                    raise ValueError("Repair incomplete or incorrect campaign results before comparing benchmark progress.")
        exact = sum(step["sides"]["candidate"]["result"]["outcome"] in {"verified", "refuted"} for step in steps)
        comparison = report["comparison"]
        series = fingerprint({"corpus": corpus_sha, "manifest": report["manifest_sha256"],
                              "baseline": comparison["baseline"], "starting_corpus": report["starting_corpus"],
                              "budget": report["budget"]})
        candidate = fingerprint(comparison["candidate"])
    except (TypeError, KeyError, AttributeError):
        raise ValueError("The Lean campaign is missing its paired development measurements.") from None
    return store.query("verifier", "SELECT * FROM rm.record_lean_benchmark(%s,%s,%s,%s,%s,%s)",
                       checksum, corpus_sha, series, candidate, exact, implementation)


def scan(store, limit, *, campaign=None):
    implementation = lean_shadow.implementation_sha256()

    def record():
        measured = _benchmark(store, campaign, implementation) if campaign is not None else []
        observed = store.query("verifier", "SELECT * FROM rm.scan_lean_rechecks(%s,%s)", implementation, limit)
        return {"implementation": implementation, "new": [*measured, *observed], "model_calls": 0}

    return store.transaction("verifier", record)


def status(store, limit):
    if type(limit) is not int or not 1 <= limit <= 128:
        raise ValueError("Choose a Lean recheck listing limit from 1 to 128.")
    implementation = lean_shadow.implementation_sha256()
    rows = store.query("human", """
        SELECT id, kind, implementation, regression_id, detail, observed_at, status,
               generation, report_sha256, verification_attempt_id, finished_at,
               implementation = %s AS current_implementation,
               status = 'queued' AND (q.origin_database = bound.database_id
                 OR q.origin_workspace = bound.workspace_id) AS needs_restore
        FROM rm.lean_recheck_status q CROSS JOIN rm.current_workspace_binding() bound
        ORDER BY observed_at DESC, id LIMIT %s
    """, implementation, limit)
    return {"implementation": implementation, "items": rows}


def _one(store, implementation, retry_before, *, lean, mathlib_project):
    def execute():
        queue = store.query("verifier", "SELECT * FROM rm.claim_lean_recheck(%s,%s)", implementation, retry_before)
        if not queue:
            return None
        [entry] = queue
        identifier = entry["id"]
        regression = entry["regression_id"]
        generation = entry["generation"]
        try:
            result = lean_incident_regression.replay(regression, lean=lean, mathlib_project=mathlib_project,
                                                    execution_token=f"{identifier}:{generation}")
        except DatabaseOperationError:
            raise
        except (OSError, ValueError) as error:
            result = {"status": "inconclusive", "model_calls": 0, "execution": None, "error": str(error)[:2000]}
        if result["status"] == "deferred":
            return {"id": identifier, "generation": generation, "status": "deferred", "model_calls": 0}
        replay = (result.get("execution") or {}).get("attempt_id")
        receipt = evaluation.archive().put({"format_version": 1, "kind": "lean-regression-recheck", "split": "development",
                                            "recheck_id": str(identifier), "regression_id": str(regression),
                                            "generation": generation, "implementation": implementation, "result": result})
        [saved] = store.query("verifier", "SELECT * FROM rm.finish_lean_recheck(%s,%s,%s,%s,%s)", identifier,
                              generation, result["status"], receipt["sha256"], replay)
        return {**saved, "regression_id": regression, "report": receipt, "model_calls": 0}

    # A crashed worker releases the row lock. The same attempt generation has
    # the same verification job key, so a completed check can be recovered.
    return store.transaction("verifier", execute)


def work(limit, *, retry=False, lean=None, mathlib_project=None):
    if type(limit) is not int or not 1 <= limit <= 32:
        raise ValueError("Choose a Lean recheck work limit from 1 to 32.")
    implementation = lean_shadow.implementation_sha256()
    results = []
    retry_before = datetime.now(timezone.utc) if retry else None
    with Store() as store:
        for _ in range(limit):
            result = _one(store, implementation, retry_before, lean=lean, mathlib_project=mathlib_project)
            if result is None:
                break
            results.append(result)
            if result["status"] == "deferred":
                break
        [origin] = store.query("human", """
            SELECT count(*) AS count FROM rm.lean_recheck_status q CROSS JOIN rm.current_workspace_binding() bound
            WHERE q.implementation = %s AND q.status = 'queued'
              AND (q.origin_database = bound.database_id OR q.origin_workspace = bound.workspace_id)
        """, implementation)
    return {"implementation": implementation, "runs": results, "needs_restore": origin["count"], "model_calls": 0}
