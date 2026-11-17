"""Seal the component change behind a paired, fixed-response evaluation."""

from hashlib import sha256
from pathlib import Path

from . import evaluation


_KIND = "component_comparison"
_UNCHANGED_FIELDS = {"model_revision", "settings_sha256"}


def _name(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 250


def _change(protocol, body):
    fields = {"format_version", "kind", "controls", "baseline", "candidate"}
    if (not isinstance(protocol, dict) or protocol.keys() != fields
            or type(protocol["format_version"]) is not int or protocol["format_version"] != 1
            or protocol["kind"] not in ("ablation", "challenger")):
        raise ValueError("A version 1 component comparison needs an ablation or challenger manifest.")
    controls = protocol["controls"]
    if (not isinstance(controls, dict) or controls.keys() != _UNCHANGED_FIELDS
            or not _name(controls["model_revision"])
            or not isinstance(controls["settings_sha256"], str)
            or not evaluation.HEX.fullmatch(controls["settings_sha256"])):
        raise ValueError("Declare the common model revision and unchanged experiment settings digest.")
    if body["baseline"]["version"]["model"] != body["candidate"]["version"]["model"]:
        raise ValueError("A paired component comparison needs the same model ID on both sides.")

    inventories = []
    for side in ("baseline", "candidate"):
        entry = protocol[side]
        if (not isinstance(entry, dict) or entry.keys() != {"response_sha256", "components"}
                or entry["response_sha256"] != body["inputs"][side]["sha256"]):
            raise ValueError(f"The component manifest does not bind the exact {side} response archive.")
        components = entry["components"]
        if not isinstance(components, dict) or any(not _name(name) or not _name(version)
                                                    for name, version in components.items()):
            raise ValueError("Each component needs a name and a version.")
        inventories.append(components)

    old, new = inventories
    changed = [name for name in old.keys() | new.keys() if old.get(name) != new.get(name)]
    if len(changed) != 1:
        raise ValueError("A paired comparison must change exactly one declared component.")
    name, = changed
    removed = name not in new
    if removed != (protocol["kind"] == "ablation"):
        raise ValueError("An ablation removes a component, and a challenger changes or adds one.")
    return {"kind": protocol["kind"], "component": name, "baseline_version": old.get(name),
            "candidate_version": new.get(name), "declared_controls": controls}


def _paired(body):
    groups = {group: dict(both_matched=0, baseline_only=0, candidate_only=0, neither_matched=0)
              for group in evaluation.GROUP_EXPECTATIONS}
    labels = {(True, True): "both_matched", (True, False): "baseline_only",
              (False, True): "candidate_only", (False, False): "neither_matched"}
    for identifier, previous in body["baseline"]["cases"].items():
        candidate = body["candidate"]["cases"][identifier]
        outcomes = previous["expected"] == previous["observed"], candidate["expected"] == candidate["observed"]
        groups[previous["group"]][labels[outcomes]] += 1
    return groups


def _seal(body, comparison_ref, protocol):
    change = _change(protocol, body)
    artifacts = evaluation.archive()
    comparison = {
        **change, "manifest": artifacts.put(protocol), "case_evaluation": comparison_ref,
        "paired_outcomes": _paired(body),
    }
    artifact = artifacts.put({
        "format_version": 1, "mode": _KIND, "algorithm_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "component_comparison": comparison,
    })
    return {**body, "component_comparison": comparison}, artifact


def compare(development, holdout, baseline, candidate, split, protocol):
    body, case_ref = evaluation.compare(development, holdout, baseline, candidate, split)
    return _seal(body, case_ref, protocol)


def reproduce(digest):
    if not isinstance(digest, str) or not evaluation.HEX.fullmatch(digest):
        raise ValueError("A report needs its 64-character SHA-256 hash.")
    ref = {"uri": f"urn:sha256:{digest}", "sha256": digest}
    artifacts = evaluation.archive()
    saved = artifacts.read_json(ref)
    if saved.get("mode") != _KIND:
        return evaluation.reproduce(digest)
    if saved.get("algorithm_sha256") != sha256(Path(__file__).read_bytes()).hexdigest():
        raise ValueError("Replay this comparison with its recorded component evaluator revision.")

    comparison = saved["component_comparison"]
    protocol = artifacts.read_json(comparison["manifest"])
    body, case_ref = evaluation.reproduce(comparison["case_evaluation"]["sha256"])
    replayed_body, replayed_ref = _seal(body, case_ref, protocol)
    if replayed_ref != ref:
        raise ValueError("The replay differs from the recorded component comparison.")
    return replayed_body, replayed_ref


def summary(body, artifact):
    report = evaluation.summary(body, artifact)
    if "component_comparison" in body:
        report["component_comparison"] = body["component_comparison"]
    return report
