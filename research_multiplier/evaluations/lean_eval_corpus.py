"""Review disjoint Lean evaluation inputs before collecting experimental results."""

from uuid import UUID

from research_multiplier import evaluation, lake_scope, lean_source, local_lemma_kernel
from research_multiplier.evaluations import lean_shadow
from research_multiplier.workflows import lean_workflow
from research_multiplier.storage.postgres import Store
from research_multiplier.protocol.serialization import fingerprint


_EXPECTED = {"verified", "refuted", "open"}
_SPLITS = ("development", "holdout")
_MAX_CASES = 128
MIN_ABLATION_CASES = 10


def _entries(document):
    if (not isinstance(document, dict) or set(document) != {"format_version", *_SPLITS}
            or type(document["format_version"]) is not int or document["format_version"] != 1):
        raise ValueError("Use a version 1 corpus containing development and holdout statements.")
    ids = set()
    result = {}
    for split in _SPLITS:
        rows = document[split]
        if not isinstance(rows, list) or not 1 <= len(rows) <= _MAX_CASES:
            raise ValueError("Provide 1 to 128 reviewed statements per corpus split.")
        entries = {}
        for row in rows:
            if (not isinstance(row, dict) or set(row) != {"statement_id", "expected"}
                    or not isinstance(row["expected"], str) or row["expected"] not in _EXPECTED):
                raise ValueError("Each corpus case needs a reviewed Lean statement UUID and expected outcome.")
            try:
                identifier = str(UUID(row["statement_id"]))
            except (AttributeError, TypeError, ValueError):
                raise ValueError("Use an exact Lean statement UUID.") from None
            if identifier in ids:
                raise ValueError("A Lean statement may occur only once across the corpus.")
            ids.add(identifier)
            entries[identifier] = row["expected"]
        result[split] = entries
    return result


def seal(document, actor, reason, *, lean=None, mathlib_project=None):
    splits = _entries(document)
    observed = set()
    enrolled = {}
    runtimes = {}
    with Store() as store:
        for split, entries in splits.items():
            rows = []
            for identifier, expected in sorted(entries.items()):
                scope = lean_shadow._case(store, identifier)
                if any(lean_shadow._activity(store, scope).values()):
                    raise ValueError("Seal the evaluation inputs before starting proof work on them.")
                statement = lean_workflow._statement(store, "verifier", identifier)
                env_key = statement["body"]["environment"]["sha256"]
                if env_key not in runtimes:
                    runtimes[env_key] = local_lemma_kernel.verify_environment(
                        statement, lean=lean, mathlib_project=mathlib_project)
                _, _, trusted, _, _ = lean_source._trusted_scope(store, statement)
                identity = lake_scope.goal_identity(runtimes[env_key], trusted)
                if identity in observed:
                    raise ValueError("A Lean proposition may occur only once across the sealed corpus, including renamed binders.")
                observed.add(identity)
                rows.append({"statement_id": identifier, "expected": expected,
                             "scope_sha256": scope["scope_sha256"], "goal_sha256": identity})
            enrolled[split] = rows
        body = {"format_version": 1, **enrolled}
        ref = evaluation.archive().put(body)
        result, = store.query("human", "SELECT * FROM rm.seal_lean_eval_corpus(%s,%s,%s,%s)",
                              ref["sha256"], store.json(body), actor, reason)
    return {**result, "artifact": ref, **{split: len(rows) for split, rows in enrolled.items()}}


def read(store, checksum, role):
    if not isinstance(checksum, str) or not evaluation.HEX.fullmatch(checksum):
        raise ValueError("Supply the SHA-256 of a reviewed Lean evaluation corpus.")
    rows = store.query(role, "SELECT body FROM rm.lean_eval_corpora WHERE sha256 = %s", checksum)
    if not rows:
        raise ValueError("The selected Lean evaluation corpus is not sealed in this workspace.")
    document = evaluation.archive().read_json({"uri": f"urn:sha256:{checksum}", "sha256": checksum})
    if fingerprint(document) != checksum or document != rows[0]["body"]:
        raise ValueError("The sealed Lean evaluation corpus changed.")
    return document


def verify_steps(document, split, steps):
    expected = {row["statement_id"]: (row["expected"], row["scope_sha256"]) for row in document[split]}
    actual = {step["statement_id"]: (step["expected"], step["scope"]["scope_sha256"]) for step in steps}
    if len(expected) != len(document[split]) or len(actual) != len(steps) or actual != expected:
        raise ValueError("Run the complete reviewed corpus split using its unchanged exact scopes and outcomes.")


def require_strategy_coverage(document, baseline, candidate):
    if baseline != candidate and len(document["development"]) < MIN_ABLATION_CASES:
        raise ValueError("A sealed Lean strategy comparison needs at least ten distinct development statements.")
