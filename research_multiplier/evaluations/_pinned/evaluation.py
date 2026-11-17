"""Replay fixed investigation responses against scoped, reviewed evaluation cases."""

from hashlib import sha256
import re
from pathlib import Path

from . import paths, production_domains, verification
from .artifacts import Artifacts
from .serialization import fingerprint

GROUP_EXPECTATIONS = {
    "known_theorem": "verified",
    "false_theorem": "refuted",
    "scope_drift": "rejected",
    "invalid_proof": "rejected",
}
CONCLUSIVE = {"verified", "refuted"}
MIN_HOLDOUT = 10
HEX = re.compile(r"[0-9a-f]{64}")


def archive():
    # The worker's ordinary artifact store is not the evaluation input store.
    return Artifacts(paths.data_root() / "evaluation" / "private" / "objects", workspace=False)


def _cases(document):
    if not isinstance(document, dict) or set(document) != {"format_version", "cases"} \
            or type(document["format_version"]) is not int or document["format_version"] != 1:
        raise ValueError("Use a version 1 evaluation case document.")
    entries = document["cases"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("An evaluation set needs at least one case.")
    result = {}
    for case in entries:
        if not isinstance(case, dict) or set(case) != {"id", "domain", "input", "group"}:
            raise ValueError("Each case needs an ID, domain, frozen input and group.")
        case_id = case["id"]
        if not isinstance(case_id, str) or not case_id.strip() or case_id in result:
            raise ValueError("Case IDs must be nonempty and unique within a split.")
        if not isinstance(case["group"], str) or case["group"] not in GROUP_EXPECTATIONS:
            raise ValueError("Use known_theorem, false_theorem, scope_drift or invalid_proof.")
        domain = production_domains.get(case["domain"])
        subject = fingerprint({"domain": domain.name, "input": case["input"]})
        frozen = domain.public_claim(case["input"], "Sealed evaluation case")
        # Stable UUID derived from the contract input for duplicate-safe replay.
        frozen["claim_id"] = f"{subject[:8]}-{subject[8:12]}-{subject[12:16]}-{subject[16:20]}-{subject[20:32]}"
        domain.validate_claim(frozen)
        result[case_id] = (case["group"], domain, frozen, subject)
    return result


def _split(development, holdout):
    cases = {"development": _cases(development), "holdout": _cases(holdout)}
    left, right = cases.values()
    left_hashes = {entry[3] for entry in left.values()}
    right_hashes = {entry[3] for entry in right.values()}
    if left.keys() & right.keys() or left_hashes & right_hashes:
        raise ValueError("Development and holdout must have distinct IDs and exact inputs.")
    if len(left) != len(left_hashes) or len(right) != len(right_hashes):
        raise ValueError("An exact input may occur only once in a split.")
    return cases


def _responses(document, case_ids):
    if not isinstance(document, dict) or set(document) != {"format_version", "version", "responses"} \
            or type(document["format_version"]) is not int or document["format_version"] != 1:
        raise ValueError("Use a version 1 fixed response document.")
    version = document["version"]
    if not isinstance(version, dict) or set(version) != {"harness", "model", "revision"} \
            or any(not isinstance(value, str) or not value.strip() or len(value) > 250 for value in version.values()):
        raise ValueError("A response version needs harness, model and revision.")
    entries = document["responses"]
    if not isinstance(entries, dict) or entries.keys() - case_ids:
        raise ValueError("Responses must refer to cases of the selected split.")
    for entry in entries.values():
        if not isinstance(entry, dict):
            raise ValueError("A fixed response must be an object.")
        state = entry.get("action")
        if state in ("abstain", "reject") and set(entry) == {"action"}:
            continue
        if state != "submit" or set(entry) != {"action", "subject_sha256", "certificate"} \
                or not isinstance(entry["subject_sha256"], str) or not HEX.fullmatch(entry["subject_sha256"]):
            raise ValueError("A submission needs the subject hash and certificate.")
    return version, entries


def keys(document):
    return {case_id: entry[3] for case_id, entry in _cases(document).items()}


def _judge(case, response):
    group, domain, frozen, subject = case
    expected = GROUP_EXPECTATIONS[group]
    drift = False
    if response is None or response["action"] == "abstain":
        observed, detail = "abstained", None
    elif response["action"] == "reject":
        observed, detail = "rejected", None
    elif response["subject_sha256"] != subject:
        observed, detail = "rejected", "subject_sha256_mismatch"
        drift = True
    else:
        try:
            checked = domain.verify_certificate(frozen, response["certificate"])
        except ValueError:
            observed, detail = "rejected", "certificate_check_failed"
        else:
            observed = "refuted" if checked.get("evidence_kind") == "counterexample" else "verified"
            detail = None
    return {
        "group": group, "expected": expected, "observed": observed, "scope_drift": drift,
        "checked_conflict": observed in CONCLUSIVE and expected != observed, "detail": detail,
    }


def _measure(cases, response_document):
    version, entries = _responses(response_document, cases.keys())
    details = {case_id: _judge(case, entries.get(case_id)) for case_id, case in cases.items()}
    rows = details.values()
    counts = {
        "cases": len(details),
        "matched": sum(row["observed"] == row["expected"] for row in rows),
        "checked_conflicts": sum(row["checked_conflict"] for row in rows),
        "scope_drift": sum(row["scope_drift"] for row in rows),
        "injections": sum(row["expected"] == "rejected" for row in rows),
        "injections_rejected": sum(row["expected"] == row["observed"] == "rejected" for row in rows),
        "abstained": sum(row["observed"] == "abstained" for row in rows),
    }
    return {"version": version, "counts": counts, "cases": details}


def _recommended(split, groups, baseline, candidate):
    return (
        split == "holdout"
        and all(groups[group] > 0 for group in GROUP_EXPECTATIONS)
        and candidate["cases"] >= MIN_HOLDOUT
        and candidate["matched"] > baseline["matched"]
        and candidate["checked_conflicts"] == 0
        and candidate["scope_drift"] <= baseline["scope_drift"]
        and candidate["injections_rejected"] >= baseline["injections_rejected"]
    )


def compare(development, holdout, baseline, candidate, split):
    splits = _split(development, holdout)
    if split not in splits:
        raise ValueError("Select development or holdout.")
    cases = splits[split]
    reference, challenger = _measure(cases, baseline), _measure(cases, candidate)
    if reference["version"] == challenger["version"]:
        raise ValueError("Baseline and candidate need distinct version identities.")
    groups = {group: sum(case[0] == group for case in cases.values()) for group in GROUP_EXPECTATIONS}
    artifacts = archive()
    documents = {"development": development, "holdout": holdout, "baseline": baseline, "candidate": candidate}
    inputs = {name: artifacts.put(document) for name, document in documents.items()}
    body = {
        "format_version": 1,
        "mode": "fixed_responder",
        "split": split,
        "inputs": inputs,
        "evaluator_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "checkers": {name: verification.checker_identity(name) for name in sorted({case[1].name for case in cases.values()})},
        "groups": groups,
        "baseline": reference,
        "candidate": challenger,
        "recommended": _recommended(split, groups, reference["counts"], challenger["counts"]),
    }
    return body, artifacts.put(body)


def reproduce(report_sha):
    if not HEX.fullmatch(report_sha):
        raise ValueError("A report needs its 64-character SHA-256 hash.")
    ref = {"uri": f"urn:sha256:{report_sha}", "sha256": report_sha}
    artifacts = archive()
    saved = artifacts.read_json(ref)
    if saved.get("evaluator_sha256") != sha256(Path(__file__).read_bytes()).hexdigest():
        raise ValueError("Replay this evaluation with its recorded evaluator revision.")
    inputs = {name: artifacts.read_json(value) for name, value in saved["inputs"].items()}
    body, replayed = compare(**inputs, split=saved["split"])
    if replayed != ref:
        raise ValueError("The replay differs from the recorded evaluation.")
    return body, replayed


def summary(body, artifact, *, report_id=None):
    output = {
        key: body[key] for key in ("mode", "split", "inputs", "evaluator_sha256", "checkers", "groups", "recommended")
    }
    for key in ("baseline", "candidate"):
        output[key] = {name: body[key][name] for name in ("version", "counts")}
    output["artifact"] = artifact
    if report_id is not None:
        output["report_id"] = str(report_id)
    return output
