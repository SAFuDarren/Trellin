"""Compare Lean search profiles in two separately restored public workspaces."""

from dataclasses import asdict
from hashlib import sha256
from importlib import import_module
import json
import os
from pathlib import Path
import re
from shutil import which
import signal
import subprocess
import sys
from time import monotonic

from research_multiplier import evaluation, general_lean, model_adapters
from research_multiplier.evaluations import evolution
from research_multiplier.workflows import formalization, lean_workflow, research_progress
from research_multiplier.storage import paths
from ..policy import basis, lean_budget
from ..policy.lean_search_scope import load_model_search_scope
from research_multiplier.storage.postgres import Store
from ..proof import lean_reductions, lean_search_components
from research_multiplier.protocol.serialization import fingerprint
from ..storage.artifacts import Artifacts
from ..workflows import lean_search, research_metrics, research_resume
from . import lean_shadow_build, lean_worker
from .lean_shadow_build import file_sha256

KIND = "lean-search-shadow"
CAMPAIGN_KIND = "lean-search-shadow-campaign"
ROLES = ("human", "scheduler", "agent", "verifier")
SIDES = ("baseline", "candidate")
_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROFILE_FIELDS = {
    "PROVIDER": "provider", "MODEL": "model", "FAMILY": "family", "BASE_URL": "base_url",
    "API_KEY": "api_key", "REASONING_EFFORT": "reasoning_effort", "BINARY": "binary",
    "ADAPTER": "adapter_factory", "MAX_OUTPUT_TOKENS": "max_output_tokens", "TIMEOUT_SECONDS": "timeout_seconds",
}
LOCAL_ENV = {
    "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "PYTHONPATH", "ELAN_HOME", "CODEX_HOME",
    "CLAUDE_CONFIG_DIR", "USERPROFILE", "SYSTEMROOT", "APPDATA", "LOCALAPPDATA",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "no_proxy",
}
RECEIPTS = """
    SELECT e.id::text, e.kind::text, e.review_status, e.issuance_sha256,
           report.body -> 'report' AS report,
           report.body -> 'candidate' AS candidate
    FROM rm.evidence_foundation_status e JOIN rm.lean_check_status report ON report.id = e.lean_check_id
    WHERE e.claim_id = %s AND e.contract_sha256 = %s AND report.statement_id = %s
      AND e.verdict = 'verified' AND e.diff_status = 'exact' AND NOT e.needs_recheck
      AND e.review_status <> 'returned' AND e.issuance_sha256 IS NOT NULL
      AND e.kind IN ('lean', 'counterexample')
    ORDER BY e.id
"""

_WORKING_CORPUS = """
    SELECT to_jsonb(l) AS lemma, s.statement_sha256, c.contract_sha256
    FROM rm.lean_statement_status s
    JOIN rm.claims c ON c.id = s.claim_id
    CROSS JOIN LATERAL rm.local_lean_lemma_status(s.id) l
    WHERE s.check_ready AND c.domain = 'lean-core' AND c.sensitivity = 'public'
      AND c.status IN ('open', 'proved') AND l.status = 'verified'
    ORDER BY l.id
"""
_THEOREM_CORPUS = """
    SELECT to_jsonb(t) AS theorem
    FROM rm.current_lean_core_theorems t
    JOIN rm.evidence_foundation_status e ON e.id = t.evidence_id
    JOIN rm.claims c ON c.id = e.claim_id
    WHERE c.sensitivity = 'public'
    ORDER BY t.id
"""


def implementation_sha256():
    """Bind the installed application, including execution code and migrations."""
    return lean_shadow_build.implementation_digest(_PACKAGE_ROOT)


def model_settings(config):
    """Record the actual transport capabilities without retaining credentials."""
    if config is None:
        return None
    timeout = min(config.timeout_seconds or 90, 90)
    adapter = model_adapters._adapter(config, timeout, None)
    values = {
        "provider": config.provider, "model": config.model, "family": config.family,
        "timeout_seconds": timeout, "requested_output_tokens": min(config.max_output_tokens or 3000, 3000),
        **asdict(adapter.capabilities),
    }
    if config.adapter_factory:
        module, _ = model_adapters._adapter_name(config.adapter_factory)
        adapter_file = getattr(import_module(module), "__file__", None)
        values["adapter_factory"] = config.adapter_factory
        values["adapter_source_sha256"] = file_sha256(adapter_file) if adapter_file else None
    else:
        transport = model_adapters.PROVIDERS[config.provider][0]
        if config.reasoning_effort and config.provider != "codex-cli" and transport != "chat":
            raise ValueError("The selected model adapter cannot apply the requested reasoning effort.")
        if transport == "cli":
            values["launcher_sha256"] = file_sha256(which(config.binary))
        else:
            values["endpoint"] = config.base_url.rstrip("/")
    values["reasoning_effort"] = config.reasoning_effort
    return values


def _refs(value):
    if isinstance(value, dict):
        digest = value.get("sha256")
        if (set(value) == {"uri", "sha256"} and isinstance(digest, str)
                and re.fullmatch(r"[0-9a-f]{64}", digest) and value["uri"] == f"urn:sha256:{digest}"):
            yield value
        else:
            for child in value.values():
                yield from _refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _refs(child)


def _artifact_closure(value, source, archive=None):
    remaining = list(_refs(value))
    checked = set()
    while remaining:
        ref = remaining.pop()
        checksum = ref["sha256"]
        if checksum in checked:
            continue
        try:
            content = source.read(ref)
        except (OSError, ValueError):
            raise ValueError("A public proof artifact is missing or does not match its recorded hash.") from None
        if archive is not None:
            archive.put_bytes(content)
        checked.add(checksum)
        try:
            document = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        remaining.extend(_refs(document))
    return sorted(checked)


def _corpus(store):
    """Measure all current public proof inputs even before routes are discovered."""
    working = store.query("verifier", _WORKING_CORPUS)
    native = store.query("verifier", _THEOREM_CORPUS)
    hashes = _artifact_closure([working, native], Artifacts(paths.object_store(), workspace=False))
    return {
        "sha256": fingerprint({"working": working, "theorems": native, "artifacts": hashes}),
        "working_lemmas": len(working), "published_theorems": len(native), "artifacts": len(hashes),
    }


def _identity(workspace):
    database, workspace_id = paths.database_id(workspace), paths.workspace_id(workspace)
    if database is None or workspace_id is None:
        raise ValueError("Bind the source and each restored shadow workspace before evaluation.")
    return {"database_id": str(database), "workspace_id": str(workspace_id)}


def _case(store, statement_id):
    statement, frozen, context = load_model_search_scope(store, statement_id)
    contract_hash = sha256(frozen["contract_bytes"].encode("utf-8")).hexdigest()
    scope = statement["body"]["scope"]
    packet = formalization.read(store, "id", statement["packet_id"])
    source = general_lean._type_source(context["consequent"], context["antecedents"], context["approved_modules"])
    if (contract_hash != statement["contract_sha256"] or contract_hash != frozen["contract_sha256"]
            or contract_hash != scope["claim_contract_sha256"]
            or packet["packet_sha256"] != scope["principal_packet_sha256"]
            or general_lean.artifact_ref(source.encode("utf-8")) != statement["body"]["trusted_source"]):
        raise ValueError("The reviewed Lean proposition, packet and contract differ from their frozen source.")
    basis_items = basis.versions(store, "verifier", statement["packet_id"])
    return {
        "statement_id": str(statement["id"]), "statement_sha256": statement["statement_sha256"],
        "trusted_source_sha256": statement["body"]["trusted_source"]["sha256"],
        "scope_sha256": fingerprint({"statement": statement["body"], "packet": packet["packet"],
                                     "basis": basis_items, "search_context": context}),
        "claim_id": str(statement["claim_id"]), "packet_id": str(statement["packet_id"]),
        "contract_sha256": contract_hash,
    }


def _activity(store, scope):
    claim_id = scope["claim_id"]
    return store.query("verifier", """
        SELECT EXISTS (SELECT FROM rm.research_task_status WHERE claim_id = %s) AS jobs,
               EXISTS (SELECT FROM rm.evidence_foundation_status WHERE claim_id = %s) AS receipts
    """, claim_id, claim_id)[0]


def _source(statement_id, *, corpus=False):
    with Store() as store:
        store.verify_runtime_roles(roles=("verifier",))
        scope = _case(store, statement_id)
        if any(_activity(store, scope).values()):
            raise ValueError("Take the shadow snapshot before starting proof work on the public claim.")
        if corpus:
            return scope, _corpus(store)
    return scope


def _runtime_scope(store, scope, options):
    statement = lean_workflow._statement(store, "verifier", scope["statement_id"])
    environment = statement["body"]["environment"]
    modules = (environment.get("external") or {}).get("modules", ())
    executable, mathlib = general_lean._runtime(options["lean"], options["mathlib_project"] if modules else None,
                                               modules)
    _, measured = general_lean._environment(executable, mathlib, expected=environment)
    if measured != environment:
        raise ValueError("Each shadow side must use the exact reviewed Lean and Mathlib environment.")


def _scratch_scope(store, scope, options):
    statement = lean_workflow._statement(store, "verifier", scope["statement_id"])
    existing_policy, = store.query("human", """
        SELECT EXISTS (SELECT FROM rm.research_progress_status WHERE claim_id = %s AND policy ? 'lean') AS present
    """, scope["claim_id"])
    initial = research_resume.snapshot(store, scope["claim_id"])
    report = lean_budget.inspect(scope["statement_id"])
    if (any(_activity(store, scope).values()) or existing_policy["present"] or initial["decisions"]["items"]
            or lean_search._known_lemmas(store, statement["id"]) or lean_reductions.statuses(store, statement)
            or report["reserved_calls"]):
        raise ValueError("Each side needs a fresh restoration before any Lean search activity on this claim.")
    if report["remaining_calls"] < options["rounds"]:
        raise ValueError("The restored public goal's call limit is smaller than the requested paired budget.")
    # Compare effective policies and tariffs, without disclosing their raw settings in the report.
    tariffs, = store.query("human", """
        SELECT encode(sha256(convert_to(jsonb_build_array(
            (SELECT jsonb_agg(to_jsonb(t) ORDER BY sequence_no) FROM rm.project_model_tariffs t),
            (SELECT jsonb_agg(to_jsonb(l) ORDER BY sequence_no) FROM rm.project_model_limits l),
            (SELECT jsonb_agg(to_jsonb(m) ORDER BY period) FROM rm.project_model_months m)
        )::text, 'UTF8')), 'hex') AS checksum
    """)
    return {"call_limit": report["call_limit"], "project_model_budget_sha256": tariffs["checksum"]}


def _result(store, scope, policy_id, cycles, status):
    receipts = store.query("verifier", RECEIPTS, scope["claim_id"], scope["contract_sha256"], scope["statement_id"])
    artifacts = Artifacts(paths.object_store(), workspace=False)
    for row in receipts:
        artifacts.read(row["report"])
        artifacts.read(row["candidate"])
    findings = {row["kind"] for row in receipts}
    outcome = ("conflict" if len(findings) > 1 else "verified" if findings == {"lean"}
               else "refuted" if findings == {"counterexample"} else "no_result")
    resume = research_resume.snapshot(store, scope["claim_id"])
    if resume["status"] != "open" or any(row["review_status"] != "pending" for row in receipts):
        raise ValueError("A shadow proof or refutation must remain pending researcher review.")
    [review_count] = store.query("human", """
        SELECT count(*) AS pending_scope_decisions FROM rm.research_actionable_review_inbox
        WHERE claim_id = %s AND kind NOT IN ('review_evidence', 'review_promotion')
    """, scope["claim_id"])
    metrics = research_metrics.inquiry(policy_id, details=True)
    return {
        "outcome": outcome, "search_status": status, "passes": cycles, "receipts": receipts,
        "review": {"next": resume["next"]["action"], **review_count,
                   "has_more_scope_decisions": False,
                   "pending_evidence_reviews": sum(row["review_status"] == "pending" for row in receipts),
                   "performed_by_researcher": 0, "logged_human_time": metrics["logged_human_time"]},
        "model": metrics["model"], "completed_passes": metrics["passes"],
    }


def _run(scope, options):
    policy = research_progress.propose({
        "packet_id": scope["packet_id"], "metric": "exact-closure", "actions": ["lean"],
        "lean_statement_id": scope["statement_id"], "lean_model_rounds": options["rounds"],
    })
    research_progress.approve(policy["id"], policy["policy_sha256"], KIND,
                              "Authorize only this bounded public search inside the restored evaluation workspace.")
    cycles = []
    status = "pass_limit"
    for index in range(options["rounds"] + 1):
        advance = research_progress.advance_cycle(
            "combined", 1, [policy["id"]], 1, lean=options["lean"], mathlib_project=options["mathlib_project"],
            lean_search_strategy=options["strategy"],
        )
        if advance["errors"] or not advance["lean_runs"]:
            status = "worker_error" if advance["errors"] else "idle"
            cycles.append({"pass": index + 1, "status": status, "errors": advance["errors"]})
            break
        result, = advance["lean_runs"]
        status = result["status"]
        cycles.append({"pass": index + 1, **result})
        if status != "search_continues":
            break
    with Store() as store:
        return _result(store, scope, policy["id"], cycles, status)


def _child(data):
    setting = (data.get("options") or {}).get("component_control")
    with lean_search_components.evaluation_control(setting) as control:
        result = _child_measured(data, control)
        if result is not None and control is not None:
            result["component_control"] = control
        return result


def _child_measured(data, control):
    with paths.use_workspace(data["workspace"], strict=True):
        if _identity(paths.require_workspace()) != data["identity"]:
            raise ValueError("The selected shadow workspace identity changed.")
        with Store() as store:
            store.verify_runtime_roles()
        with Store() as store:
            scope = _case(store, data["scope"]["statement_id"])
            if scope != data["scope"]:
                raise ValueError("A restored scope differs from the source's frozen Lean statement or confirmed basis.")
            if data["action"] == "read":
                policies = store.query("human", """
                    SELECT id FROM rm.research_progress_status
                    WHERE claim_id = %s AND policy #>> '{lean,statement_id}' = %s
                """, scope["claim_id"], scope["statement_id"])
                return (_result(store, scope, policies[0]["id"], [], "timeout") if len(policies) == 1 else None)
            options = data["options"]
            config = model_adapters.ModelConfig.from_env("LEAN_AUTHOR", use_active=False) if options["rounds"] else None
            _runtime_scope(store, scope, options)
            start = {
                "budget": _scratch_scope(store, scope, options),
                "corpus": _corpus(store),
                "implementation_sha256": implementation_sha256(),
                "configuration_sha256": evolution.configuration_sha256(config) if config else None,
                "effective_settings": model_settings(config),
            }
            if control is not None:
                start["component_control"] = control
            if data["action"] == "inspect":
                return start
            if start != data["starting_state"]:
                raise ValueError("The restored side's proof inputs, adapter, implementation or budget changed after paired preflight.")
        result = _run(scope, options)
        with Store() as store:
            result["starting_corpus"] = start["corpus"]
            result["ending_corpus"] = _corpus(store)
        return result


def _environment(side, config, *, case_id=None):
    env = {key: os.environ[key] for key in LOCAL_ENV if key in os.environ}
    python_path = [_PACKAGE_ROOT.parent]
    python_path.extend(Path(path).resolve() for path in env.get("PYTHONPATH", "").split(os.pathsep) if path)
    env["PYTHONPATH"] = os.pathsep.join(map(str, python_path))
    # Only this isolated database's roles enter the child.
    prefix = f"RM_SHADOW_{case_id.upper().replace('-', '_')}_" if case_id else "RM_SHADOW_"
    for role in ROLES:
        key = f"{prefix}{side.upper()}_{role.upper()}_DSN"
        if not os.environ.get(key):
            raise ValueError(f"Configure {key} for the restored {side} workspace.")
        env[f"RM_{role.upper()}_DSN"] = os.environ[key]
    if config is not None:
        for key, field in PROFILE_FIELDS.items():
            value = getattr(config, field)
            if value is not None:
                env[f"RESEARCH_MULTIPLIER_LEAN_AUTHOR_{key}"] = str(value)
    return env


def _dispatch(data, env, timeout, *, build=None):
    started = monotonic()
    command = build.command() if build else lean_worker.command(_PACKAGE_ROOT.parent)
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, env=env, cwd=data["workspace"], text=True,
                          start_new_session=True) as process:
        try:
            stdout, _ = process.communicate(lean_worker.request_text(data), timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
            process.communicate()
            return {"error": "timeout", "elapsed_seconds": round(monotonic() - started, 3)}
    response = lean_worker.read_response(stdout, process.returncode)
    return {"elapsed_seconds": round(monotonic() - started, 3), **response}


def _workspaces(roots):
    if any(a == b or a in b.parents or b in a.parents
           for index, a in enumerate(roots) for b in roots[index + 1:]):
        raise ValueError("Select separate, non-nested source and shadow workspaces.")
    identities = [_identity(root) for root in roots]
    if any(len({item[key] for item in identities}) != len(roots) for key in identities[0]):
        raise ValueError("The source and all restored sides need distinct workspace and database identities.")
    return identities


def _settings(profile, baseline_profile, *, confirm_public, rounds, lean, mathlib_project, side_timeout,
              baseline_strategy, candidate_strategy, builds=None, ablation=None):
    if type(rounds) is not int or not 0 <= rounds <= lean_search.MAX_ROUNDS or not 1 <= side_timeout <= 3600:
        raise ValueError("Choose 0 to 8 model rounds and a side timeout of 1 to 3,600 seconds.")
    if rounds and not confirm_public:
        raise ValueError("Use --confirm-public to authorize both profiles to receive the reviewed public scope.")
    if not rounds and (profile or baseline_profile):
        raise ValueError("Zero-model shadow runs compare search strategies without model profiles.")
    if builds and profile is not None and profile != baseline_profile:
        raise ValueError("Keep one shared model profile when comparing installed Lean builds.")
    strategies = dict(zip(SIDES, (baseline_strategy, candidate_strategy)))
    if any(strategy not in lean_search.SEARCH_STRATEGIES for strategy in strategies.values()):
        raise ValueError("Choose a supported Lean search strategy.")
    baseline_config = None
    if rounds:
        baseline_config = (evolution._profile_config(baseline_profile) if baseline_profile
                           else model_adapters.ModelConfig.from_env("LEAN_AUTHOR", use_active=False))
    configs = {"baseline": baseline_config,
               "candidate": evolution._profile_config(profile) if profile and not builds else baseline_config}
    effective = {side: model_settings(config) for side, config in configs.items()}
    changed = sum((effective["baseline"] != effective["candidate"], baseline_strategy != candidate_strategy,
                   bool(builds), ablation is not None))
    if changed != 1:
        raise ValueError("Change exactly one experimental variable: search strategy, effective model profile, "
                         "installed build, or supported scaffolding component.")
    if ablation is not None and baseline_strategy != "ranked":
        raise ValueError("Use the ranked Lean strategy on both sides of a component ablation.")
    caps = {(settings["requested_output_tokens"], settings["timeout_seconds"])
            for settings in effective.values() if settings is not None}
    if len(caps) > 1:
        raise ValueError("Use identical requested token and model time limits for both Lean model profiles.")
    token_cap, model_timeout = caps.pop() if caps else (None, None)
    lean_path = str(Path(lean).expanduser().absolute()) if lean and os.path.dirname(lean) else lean
    project = mathlib_project or os.environ.get("RM_LEAN_MATHLIB_PROJECT")
    options = {"rounds": rounds, "lean": lean_path,
               "mathlib_project": str(Path(project).expanduser().resolve()) if project else None}
    revision = None if builds else implementation_sha256()
    comparisons = {
        side: {
            "strategy": strategies[side], "profile": name, "effective_settings": effective[side],
            "configuration_sha256": evolution.configuration_sha256(configs[side]) if configs[side] else None,
            "implementation_sha256": builds[side].implementation_sha256 if builds else revision,
            **({"installation": builds[side].identity()} if builds else {}),
        } for side, name in zip(SIDES, (baseline_profile, profile or baseline_profile))
    }
    if ablation is not None:
        for side in SIDES:
            comparisons[side]["component_control"] = lean_search_components.control(ablation, enabled=side == "baseline")
    budget = {"model_rounds_per_side": rounds, "side_timeout_seconds": side_timeout,
              "model_timeout_seconds": model_timeout, "requested_model_output_tokens": token_cap}
    return options, configs, comparisons, budget


def _input(root, identity, scope, options, comparison, schema=None):
    settings = {key: comparison[key] for key in ("strategy", "component_control") if key in comparison}
    return {"workspace": str(root), "identity": identity, "scope": scope,
            "options": {**options, **settings},
            **({"database_schema_revision": schema} if schema else {})}


def _inspect(side, request, environment, comparison, timeout, *, build=None):
    reply = _dispatch({**request, "action": "inspect"}, environment, timeout, build=build)
    if "error" in reply:
        raise ValueError(f"The {side} shadow preflight failed. {reply['error']}")
    state = reply["result"]
    if not isinstance(state, dict) or not {"budget", "corpus"} <= state.keys():
        raise ValueError("The isolated Lean shadow worker uses an incompatible preflight protocol.")
    if any(state.get(key) != comparison.get(key) for key in
           ("implementation_sha256", "configuration_sha256", "effective_settings", "component_control")):
        raise ValueError("The isolated Lean shadow worker loaded a different implementation, model profile or component.")
    return state


def _same_start(starting, corpus):
    if any(state["corpus"] != corpus for state in starting.values()):
        raise ValueError("The source and both restored sides need the same public starting proof corpus and artifacts.")
    if starting["baseline"]["budget"] != starting["candidate"]["budget"]:
        raise ValueError("The restored sides have different starting call limits or project model budget policies.")


def _copy_receipts(result, workspace):
    """Retain exact final proof and report artifacts in the source evaluation archive."""
    receipts = result.get("receipts", ())
    root = Path(workspace) / "objects"
    if root.resolve() != root:
        raise ValueError("The shadow proof object store moved after workspace validation.")
    return _artifact_closure(list(receipts), Artifacts(root, workspace=False), evaluation.archive())


def _measure(request, environment, start, comparison, timeout, read_timeout=15, *, build=None):
    result = _dispatch({**request, "action": "run", "starting_state": start}, environment, timeout, build=build)
    if result.get("error") == "timeout" and read_timeout > 0:
        partial = _dispatch({**request, "action": "read"}, environment, read_timeout, build=build)
        if partial.get("result") is not None:
            result["result"] = partial["result"]
    if result.get("result") is not None:
        measured = result["result"]
        if not isinstance(measured, dict) or not {"outcome", "search_status", "receipts", "review", "model"} <= measured.keys():
            raise ValueError("The isolated Lean shadow worker uses an incompatible result protocol.")
        if measured.get("component_control") != comparison.get("component_control"):
            raise ValueError("The isolated Lean shadow result did not use the agreed component control.")
        result["receipt_artifacts"] = _copy_receipts(result["result"], request["workspace"])
    effective = comparison["effective_settings"] or {}
    return {**result, **request["identity"], **comparison,
            **{key: effective.get(key) for key in ("provider", "model", "reasoning_effort")}}


def _report_identity(comparisons, schema):
    if schema is None:
        return {"search_revision": comparisons["baseline"]["implementation_sha256"]}
    return {"comparison_mode": "installed_builds", "activation_eligible": False,
            "database_schema_revision": schema, "evaluation_revision": implementation_sha256(),
            "python": {"version": sys.version.split()[0], "executable_sha256": file_sha256(sys.executable)}}


def shadow(statement_id, baseline, candidate, profile=None, *, confirm_public, rounds, lean=None,
           mathlib_project=None, side_timeout=300, baseline_strategy="ranked", candidate_strategy="ranked",
           baseline_profile=None, builds=None):
    options, configs, comparisons, budget = _settings(
        profile, baseline_profile, confirm_public=confirm_public, rounds=rounds, lean=lean,
        mathlib_project=mathlib_project, side_timeout=side_timeout,
        baseline_strategy=baseline_strategy, candidate_strategy=candidate_strategy, builds=builds,
    )
    roots = [paths.require_workspace(), *map(paths.workspace_details, (baseline, candidate))]
    identities = _workspaces(roots)
    scope, corpus = _source(statement_id, corpus=True)
    builds = builds or {}
    schema = lean_shadow_build.database_revision(builds)
    identity = _report_identity(comparisons, schema)
    environments = {side: _environment(side, configs[side]) for side in SIDES}
    inputs = {side: _input(root, identity, scope, options, comparisons[side], schema)
              for side, root, identity in zip(SIDES, roots[1:], identities[1:])}
    starts = {side: _inspect(side, inputs[side], environments[side], comparisons[side], side_timeout,
                             build=builds.get(side)) for side in SIDES}
    _same_start(starts, corpus)
    results = {}
    for side in SIDES:
        if _source(statement_id, corpus=True) != (scope, corpus):
            raise ValueError("The source scope or public proof corpus changed during Lean shadow evaluation.")
        results[side] = _measure(inputs[side], environments[side], starts[side], comparisons[side], side_timeout,
                                 build=builds.get(side))
    if (_source(statement_id, corpus=True) != (scope, corpus) or builds and (
            lean_shadow_build.database_revision(builds) != schema or _report_identity(comparisons, schema) != identity)):
        raise ValueError("Source inputs or the evaluator changed during Lean shadow evaluation.")
    report = {
        "format_version": 2, "kind": KIND, "scope": scope, "source": identities[0], "sides": results,
        "starting_corpus": corpus, **identity,
        "budget": {**budget, **starts["baseline"]["budget"]},
    }
    return {**report, "report": evaluation.archive().put(report)}


def status(checksum):
    report = evaluation.archive().read_json({"uri": f"urn:sha256:{checksum}", "sha256": checksum})
    if report.get("kind") not in {KIND, CAMPAIGN_KIND} or report.get("source") != _identity(paths.require_workspace()):
        raise ValueError("The artifact is not a Lean search shadow report for this workspace.")
    return report
