"""Verify sufficient conditions for a frozen goal and discharge them from its checked lemmas."""

from research_multiplier import composed_lean, general_lean, lean_parameters, local_lemma_kernel, local_lemmas
from research_multiplier.workflows import lean_workflow
from research_multiplier.storage import paths
from .lean_decomposition import introduce
from ..storage.artifacts import Artifacts
from ..final_lean_local import FinalProofRejected, check
from research_multiplier.storage.postgres import Store


def _input(document):
    if (not isinstance(document, dict) or set(document) != {"format_version", "obligations", "proof"}
            or type(document["format_version"]) is not int or document["format_version"] != 1
            or not isinstance(document["proof"], dict)):
        raise ValueError("Supply a version 1 reduction with obligations and a structured implication proof.")
    obligations = document["obligations"]
    if (not isinstance(obligations, list) or not 1 <= len(obligations) <= 8
            or any(not isinstance(item, str) or not item.strip() for item in obligations)
            or len(set(obligations)) != len(obligations)):
        raise ValueError("Supply 1 to 8 distinct, nonempty Lean propositions as proof obligations.")
    return obligations, document["proof"]


def statuses(store, statement):
    routes = store.query("verifier", """
        SELECT * FROM rm.lean_reduction_status(%s) ORDER BY recorded_at, implication_id
    """, statement["id"])
    return [{**route, "implication_id": str(route["implication_id"])} for route in routes]


def _application(identifier, arguments):
    terms = [{"ref": identifier}, *arguments]
    return {"apply": terms} if arguments else terms[0]


def _proof(route, premise_count):
    hypotheses = [{"use": index} for index in range(premise_count)]
    reduced = _application(route["implication_id"], hypotheses)
    obligations = [_application(item["lemma_id"], hypotheses) for item in route["obligations"]]
    body = {"apply": [reduced, *obligations]}
    body = introduce(premise_count, body)
    return {"format_version": 1, "proof": body}


def current_result(routes):
    """Return a current, unreturned receipt for an already checked reduction."""
    for route in routes:
        if route["status"] == "goal_checked" and route["review_status"] in {"pending", "approved"}:
            return {"evidence_id": str(route["evidence_id"]), "used_reduction": route["implication_id"],
                    "reused_evidence": True}
    return {}


def _check_route(statement, route, checked_runtime, output, retry, reuse_current, on_receipt):
    implication = route["implication_id"]
    reused = False

    def record(receipt_store, report):
        nonlocal reused
        if reuse_current and receipt_store.query("verifier", """
            SELECT 1 FROM rm.lean_reduction_results result
            JOIN rm.evidence_foundation_status evidence ON evidence.id = result.evidence_id
            WHERE result.implication_id = %s AND result.evidence_id = %s
              AND NOT evidence.needs_recheck AND evidence.review_status IN ('pending', 'approved')
        """, implication, report["evidence_id"]):
            reused = True
            return
        receipt_store.query("verifier", "SELECT rm.record_lean_reduction_result(%s,%s)",
                            implication, report["evidence_id"])
        if on_receipt is not None:
            on_receipt(receipt_store, report, implication)

    scope = statement["body"]["scope"]
    argument_count = len(scope["lean_hypotheses"]) + len(scope.get("lean_parameters", ()))
    goal = check(statement["id"], _proof(route, argument_count), output or paths.runs() / "lean",
                 retry=retry, checked_runtime=checked_runtime, on_receipt=record)
    result = {"used_reduction": implication, "evidence_id": goal["evidence_id"]}
    return {**result, **({"reused_evidence": True} if reused else {"goal": goal})}


def close_ready(store, statement, *, routes=None, lean=None, mathlib_project=None, output=None,
                retry=False, checked_runtime=None, on_receipt=None, reuse_current=False, attempts=None,
                continue_work=None):
    """Check ready reductions in order, advancing only past candidate-local failures."""
    if routes is None:
        routes = statuses(store, statement)
    if reuse_current:
        existing = current_result(routes)
        if existing:
            return existing
    recorded = attempts if attempts is not None else []
    failed = {(row["implication_id"], tuple(row["obligation_lemma_ids"]))
              for row in recorded if row["status"] == "closure_rejected"} if not retry else set()
    latest_failure = None
    checks = 0
    for route in routes:
        if route["status"] != "ready":
            continue
        identity = (route["implication_id"], tuple(item["lemma_id"] for item in route["obligations"]))
        if identity in failed:
            continue
        if (attempts is not None and checks == 8) or (continue_work is not None and not continue_work()):
            return {}
        checks += 1
        if checked_runtime is None:
            checked_runtime = local_lemma_kernel.verify_environment(statement, lean=lean, mathlib_project=mathlib_project)
        try:
            return _check_route(statement, route, checked_runtime, output, retry, reuse_current, on_receipt)
        except (FinalProofRejected, composed_lean.NativeCompositionBlocked) as error:
            if getattr(error, "kind", None) == "lake_scope_review_required":
                raise
            # A changed runtime, withdrawn scope or failed receipt is global to this search.
            # Only a failed proof attempt may be bypassed.
            checked_runtime.assert_current()
            current = lean_workflow._statement(store, "verifier", statement["id"])
            if (not current["check_ready"] or current["statement_sha256"] != statement["statement_sha256"]):
                raise ValueError("The confirmed Lean scope changed during reduction verification.") from error
            if reuse_current:
                existing = current_result(statuses(store, statement))
                if existing:
                    return existing
            failed.add(identity)
            failed_attempt = {"implication_id": identity[0], "obligation_lemma_ids": list(identity[1]),
                              "status": "closure_rejected", "reason": str(error)[:500]}
            if getattr(error, "attempt_id", None):
                failed_attempt["attempt_id"] = error.attempt_id
            recorded.append(failed_attempt)
            latest_failure = error
    if latest_failure is not None and attempts is None:
        raise latest_failure
    return {}


def refutation(statement, proposition):
    """Name the negation of an obligation under the complete frozen scope."""
    scope = statement["body"]["scope"]
    hypotheses = lean_parameters.Hypotheses(scope["lean_hypotheses"], scope.get("lean_parameters", ()))
    return f"_root_.Not (\n{general_lean._implication(proposition, hypotheses)}\n)"


def with_refutations(routes, statement, known):
    """Use only a live exact local negation to disqualify an unfinished route."""
    verified = {row["proposition"]: identifier for identifier, row in known.items() if row["status"] == "verified"}
    result = []
    for route in routes:
        if route["status"] != "awaiting_obligations":
            result.append(route)
            continue
        witnesses = [{"proposition": item["proposition"], "lemma_id": identifier}
                     for item in route["obligations"] if not item["lemma_id"]
                     for identifier in [verified.get(refutation(statement, item["proposition"]))] if identifier]
        result.append({**route, "status": "refuted_obligation", "refuted_obligations": witnesses}
                      if witnesses else route)
    return result


def register_checked(store, artifacts, statement, subject, contract, known, document, *,
                     checked_runtime, retry, on_receipt=None):
    """Check the exact implication before registering its unproved obligations."""
    obligations, proof = _input(document)
    proposition = general_lean._implication(statement["body"]["theorem_type"], obligations)
    if len(proposition) > general_lean.MAX_TYPE_CHARS:
        raise ValueError("The full reduction exceeds the Lean proposition size limit.")
    scope = statement["body"]["scope"]
    if len(scope["lean_hypotheses"]) + len(scope.get("lean_parameters", ())) > 23:
        raise ValueError("The structured reduction supports up to 23 shared parameters and frozen hypotheses in total.")

    def register(row):
        store.query("verifier", "SELECT rm.record_lean_reduction(%s,%s,%s::text[])",
                    statement["id"], row["id"], obligations)
        if on_receipt is not None:
            on_receipt(row)

    return local_lemmas._derive(
        store, artifacts, statement, subject, contract, known,
        {"name": "Reduction", "proposition": proposition, "proof": proof}, {},
        checked_runtime=checked_runtime, retry=retry, on_receipt=register,
    )


def reductions(statement_id, *, document=None, work=False, lean=None, mathlib_project=None, output=None, retry=False):
    with Store() as store:
        statement = lean_workflow._statement(store, "verifier", statement_id)
        if statement["body"].get("checker") != general_lean.CHECKER:
            raise ValueError("Choose a frozen Lean Core research goal for the reduction.")
        selected = None
        if document is not None:
            artifacts = Artifacts(paths.object_store(), workspace=False)
            subject = {"claim_id": str(statement["claim_id"]), "contract_sha256": statement["contract_sha256"]}
            contract = artifacts.put(store.frozen(statement["claim_id"])["contract_bytes"], raw=True)
            selected = local_lemma_kernel.verify_environment(statement, lean=lean, mathlib_project=mathlib_project)
            known = {str(row["id"]): row for row in local_lemmas._rows(store, statement["id"])}
            register_checked(store, artifacts, statement, subject, contract, known, document,
                             checked_runtime=selected, retry=retry)
        routes = statuses(store, statement)
        if any(item["status"] == "awaiting_obligations" for item in routes):
            available = {str(row["id"]): row for row in local_lemmas._rows(store, statement["id"])}
            routes = with_refutations(routes, statement, available)
        result = {"statement_id": str(statement["id"]), "reductions": routes}
        if work:
            closed = close_ready(store, statement, routes=routes, lean=lean, mathlib_project=mathlib_project,
                                 output=output, retry=retry, checked_runtime=selected)
            if closed:
                result.update(goal=closed["goal"], used_reduction=closed["used_reduction"],
                              reductions=statuses(store, statement))
        return result
