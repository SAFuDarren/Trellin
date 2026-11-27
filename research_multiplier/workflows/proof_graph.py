"""Read the exact versions and assurance records that back a proof graph."""

from uuid import UUID

from research_multiplier import general_lean, lean_parameters
from research_multiplier.workflows import claim_references, proof_ledger


class ClaimGraphNotFound(ValueError):
    """The requested claim has no frozen graph."""


_CURRENT_PACKET = """
    SELECT p.id, p.version, p.approved
    FROM rm.formalization_status p
    WHERE p.claim_id = claim.id AND NOT p.superseded
    ORDER BY p.approved DESC, p.proposed_at DESC, p.id LIMIT 1
"""
_CURRENT_STATEMENT = """
    SELECT s.id, s.version, s.statement_sha256, s.approved, s.check_ready, s.body
    FROM rm.lean_statement_status s
    WHERE s.packet_id = packet.id AND s."current"
    ORDER BY s.proposed_at DESC, s.id LIMIT 1
"""

_ANCHOR = """
selected AS (
    SELECT claim.id, claim.title, claim.statement, claim.domain, claim.contract_snapshot, status.status
    FROM rm.claims claim
    JOIN rm.claim_foundation_status status ON status.id = claim.id
    WHERE claim.id = %s::uuid AND claim.contract_sha256 IS NOT NULL
),
packet AS (
    SELECT current_packet.* FROM selected claim CROSS JOIN LATERAL (""" + _CURRENT_PACKET + """) current_packet
),
statement AS (
    SELECT current_statement.* FROM packet CROSS JOIN LATERAL (""" + _CURRENT_STATEMENT + """) current_statement
)
"""

_GRAPH = """
WITH """ + _ANCHOR + """,
frozen AS MATERIALIZED (
    SELECT basis.* FROM packet CROSS JOIN LATERAL rm.proof_graph_frozen_basis(packet.id) basis
),
lemmas AS MATERIALIZED (
    SELECT l.id, l.proposition, l.status, l.recorded_at, l.verification_attempt_id,
           l.body -> 'basis_version_ids' AS basis_refs,
           l.body -> 'local_lemma_ids' AS local_refs,
           l.body -> 'dependencies' AS dependencies,
           l.body ->> 'expected_type' AS checked_type, l.body #>> '{report,sha256}' AS report_sha256
    FROM statement CROSS JOIN LATERAL rm.local_lean_lemma_status(statement.id) l
),
checks AS MATERIALIZED (
    SELECT c.id, c.review_status, c.recorded_at, c.needs_recheck, c.evidence_id, c.check_sha256,
           c.body ->> 'target' AS target, c.body ->> 'actual_type' AS checked_type,
           c.body ->> 'statement_type' AS statement_type, c.body -> 'dependencies' AS dependencies,
           c.body -> 'basis_version_ids' AS basis_refs, c.body -> 'direct_basis_version_ids' AS direct_refs,
           c.body -> 'local_lemma_ids' AS local_refs, c.body -> 'accepted' AS accepted,
           (SELECT jsonb_agg(used.version_id ORDER BY used.version_id)
            FROM rm.verification_basis_use used
            WHERE used.check_id = c.id AND used.dependency_kind = 'actual') AS actual_refs,
           c.body ->> 'source_module' AS source_module, c.body ->> 'source_declaration' AS declaration
    FROM statement JOIN rm.lean_check_status c ON c.statement_id = statement.id
),
items AS (
    SELECT version_id FROM frozen
    UNION SELECT b.item_version_id FROM packet CROSS JOIN LATERAL rm.get_packet_basis(packet.id) b
    UNION SELECT item.id::uuid FROM (
        SELECT basis_refs FROM lemmas UNION ALL SELECT coalesce(actual_refs, direct_refs, basis_refs) FROM checks
    ) report CROSS JOIN LATERAL jsonb_array_elements_text(
        CASE jsonb_typeof(report.basis_refs) WHEN 'array' THEN report.basis_refs ELSE '[]'::jsonb END
    ) AS item(id)
),
basis AS (
    SELECT b.id, b.logical_key, b.version, b.version_sha256, b.origin, b.body,
           b.needs_recheck, b.admitted, frozen.item_label,
           EXISTS (SELECT FROM rm.formal_item_retirements r WHERE r.version_id = b.id) AS retired
    FROM items JOIN rm.formal_item_status b ON b.id = items.version_id
    LEFT JOIN frozen ON frozen.version_id = b.id
),
reductions AS (
    SELECT r.* FROM statement CROSS JOIN LATERAL rm.lean_reduction_status(statement.id) r
),
advance AS (
    SELECT run.result ->> 'status' AS status, run.result ->> 'reason' AS reason,
           run.result -> 'route_attempts' AS attempts
    FROM packet JOIN rm.research_progress_status p ON p.packet_id = packet.id
    JOIN statement ON p.policy #>> '{lean,statement_id}' = statement.id::text
    JOIN rm.lean_progress_last_run run ON run.policy_id = p.id
    WHERE p.approved_at IS NOT NULL
    ORDER BY run.recorded_at DESC, p.id LIMIT 1
)
SELECT selected.id, selected.title, selected.statement, selected.domain, selected.status, selected.contract_snapshot,
       (SELECT to_jsonb(packet_row) FROM packet packet_row) AS packet,
       (SELECT to_jsonb(lean_row) FROM statement lean_row) AS lean,
       (SELECT to_jsonb(advance_row) FROM advance advance_row) AS advance,
       coalesce((SELECT jsonb_agg(to_jsonb(b) ORDER BY b.item_label NULLS LAST, b.logical_key, b.version, b.id)
                 FROM basis b), '[]'::jsonb) AS basis,
       coalesce((SELECT jsonb_agg(to_jsonb(l) ORDER BY l.recorded_at, l.id) FROM lemmas l), '[]'::jsonb) AS lemmas,
       coalesce((SELECT jsonb_agg(to_jsonb(c) ORDER BY c.recorded_at, c.id) FROM checks c), '[]'::jsonb) AS checks,
       coalesce((SELECT jsonb_agg(to_jsonb(r) ORDER BY r.recorded_at, r.implication_id) FROM reductions r),
                '[]'::jsonb) AS reductions
FROM selected
"""

_IMPACT = """
WITH RECURSIVE """ + _ANCHOR + """,
affected AS MATERIALIZED (
    SELECT a.* FROM packet
    CROSS JOIN LATERAL rm.proof_graph_basis_impact(packet.id, %s::uuid) a
),
lemma_titles AS (
    SELECT l.id, l.proposition FROM statement
    CROSS JOIN LATERAL rm.local_lean_lemma_status(statement.id) l
    JOIN affected a ON a.kind = 'local-lemma' AND a.id = l.id
),
identified AS (
    SELECT a.*, claim.title AS claim_title,
           coalesce(item.body ->> 'natural', item.logical_key, lemma.proposition, claim.title) AS title
    FROM affected a
    LEFT JOIN rm.claims claim ON claim.id = a.claim_id
    LEFT JOIN rm.formal_item_status item ON a.kind = 'item' AND item.id = a.id
    LEFT JOIN lemma_titles lemma ON a.kind = 'local-lemma' AND lemma.id = a.id
),
pending AS (
    SELECT r.implication_id, step.position, step.body ->> 'proposition' AS title
    FROM statement CROSS JOIN LATERAL rm.lean_reduction_status(statement.id) r
    JOIN affected a ON a.kind = 'local-lemma' AND a.id = r.implication_id
    CROSS JOIN LATERAL jsonb_array_elements(r.obligations) WITH ORDINALITY AS step(body, position)
    WHERE r.status = 'awaiting_obligations' AND step.body ->> 'lemma_id' IS NULL
)
SELECT coalesce((SELECT jsonb_agg(to_jsonb(a) ORDER BY a.direct DESC, a.kind, a.id)
                 FROM identified a), '[]'::jsonb) AS affected,
       coalesce((SELECT jsonb_agg(to_jsonb(p) ORDER BY p.implication_id, p.position)
                 FROM pending p), '[]'::jsonb) AS pending
"""


def _id(kind, identifier):
    return f"{kind}:{identifier}"


def _statement(formal):
    if not isinstance(formal, dict):
        return {}
    lean = formal.get("lean")
    code = lean.get("proposition") if isinstance(lean, dict) else lean
    formula = formal.get("latex") or formal.get("formula")
    return {
        **({"formalization": code} if isinstance(code, str) and code else {}),
        **({"formula": formula} if isinstance(formula, str) and formula else {}),
        **({"parameters": lean["parameters"]} if isinstance(lean, dict) and lean.get("parameters") else {}),
    }


def _goal(row, identifier):
    contract = row["contract_snapshot"]
    target = contract.get("formal_skeleton", {}).get("target")
    formal = _statement(target)
    if row["lean"]:
        lean = row["lean"]["body"]
        scope = lean["scope"]
        code = lean["theorem_type"]
        if lean.get("theorem") == general_lean.THEOREM:
            hypotheses = lean_parameters.Hypotheses(scope["lean_hypotheses"], scope.get("lean_parameters", ()))
            code = lean.get("elaborated_theorem_type") or general_lean._implication(code, hypotheses)
        formal["formalization"] = code
        formal["formalization_status"] = "approved" if row["lean"]["approved"] else "pending"
    return {
        "id": identifier, "kind": "goal", "title": row["title"], "statement": row["statement"],
        "status": row["status"], "domain": row["domain"], **formal,
        "reference": {"claim_id": str(row["id"]), **({
            "statement_id": row["lean"]["id"], "statement_version": row["lean"]["version"],
            "statement_sha256": row["lean"]["statement_sha256"],
        } if row["lean"] else {})},
    }


def _basis_node(value):
    body = value["body"]
    version = value["id"]
    kind = "hypothesis" if body.get("kind") == "hypothesis" else "library"
    label = value.get("item_label")
    statement = body.get("natural")
    formal = _statement(body.get("formal"))
    source = body.get("source", {})
    lean = body.get("formal", {}).get("lean")
    citation = lean.get("declaration") if isinstance(lean, dict) else None
    status = "approved" if value["admitted"] else "pending"
    if value["retired"]:
        status = "withdrawn"
    elif value["needs_recheck"]:
        status = "needs_recheck"
    return {
        "id": _id("item", version),
        "kind": kind, "title": body.get("title") or statement or label or value["logical_key"],
        "statement": statement, "status": status, **formal,
        **({"citation": citation} if citation else {}),
        "reference": {
            "basis_version_id": version, "version": value["version"], "sha256": value["version_sha256"],
            **({"source_claim_id": source["claim_id"]} if isinstance(source, dict) and source.get("claim_id") else {}),
        },
    }


def _checked_premises(dependencies, hypotheses, parameters):
    report = dependencies.get("premise_usage")
    if report is not None:
        return {part["item"]: part.get("occurs_in_proof_term", part.get("in_proof")) for part in report}
    occurrences = dependencies.get("leading_binder_occurrence")
    if isinstance(occurrences, list):
        return {label: usage for label, usage in zip(hypotheses, occurrences[len(parameters):])}
    return {}


def _obligation_title(position):
    return f"Proof obligation {position}"


def _snapshot(row):
    root_id = _id("goal", row["id"])
    root = _goal(row, root_id)
    nodes = {root_id: root}
    edges = {}
    blockers = []
    hypotheses = [item["label"] for item in row["contract_snapshot"].get("hypotheses", [])]
    parameters = row["lean"]["body"]["scope"].get("lean_parameters", []) if row["lean"] else []
    scope_items = {}

    def connect(source, target, kind, **metadata):
        edges[(source, target, kind)] = {"source": source, "target": target, "kind": kind, **metadata}

    def checked_references(record, target, *, lemma=False):
        dependencies = record.get("dependencies") or {}
        if not lemma and record.get("accepted") is not True:
            return
        refs = record.get("direct_refs")
        if refs is None:
            refs = dependencies.get("direct_basis_version_ids")
        if refs is None and lemma:
            refs = record.get("basis_refs")
        relation = "direct"
        if refs is None:
            refs = record.get("actual_refs") or []
            relation = "recorded_dependency"
        for version in refs:
            connect(_id("item", version), target, "actual", relation=relation)
        for identifier in record.get("local_refs") or []:
            connect(_id("local-lemma", identifier), target, "actual")
        if lemma or record["target"] != "refutation":
            usage = _checked_premises(dependencies, hypotheses, parameters)
            nodes[target]["premise_usage"] = usage
            for label, observed in usage.items():
                if observed is True and label in scope_items:
                    connect(scope_items[label], target, "actual", relation="observed_binder")

    for basis in row["basis"]:
        if basis["body"].get("kind") == "goal":
            continue
        node = _basis_node(basis)
        nodes[node["id"]] = node
        if basis.get("item_label") and node["kind"] == "hypothesis":
            scope_items[basis["item_label"]] = node["id"]
            connect(node["id"], root_id, "scope")

    routes = {route["implication_id"]: route for route in row["reductions"]}
    route_number = {identifier: number for number, identifier in enumerate(routes, start=1)}
    historical_lemmas = {}
    for index, lemma in enumerate(row["lemmas"], start=1):
        identifier = _id("local-lemma", lemma["id"])
        route = routes.get(lemma["id"])
        nodes[identifier] = {
            "id": identifier, "kind": "reduction" if route else "local_lemma",
            "title": f"Checked reduction {route_number[lemma['id']]}" if route else f"Lean lemma {index}",
            "status": route["status"] if route else lemma["status"],
            "verification_status": lemma["status"],
            **({"proposition": lemma["proposition"]} if not route else {}),
            "formalization": lemma.get("checked_type") or lemma["proposition"],
            "reference": {"lemma_id": lemma["id"], "verification_attempt_id": lemma["verification_attempt_id"],
                          "sha256": lemma.get("report_sha256")},
        }
        checked_references(lemma, identifier, lemma=True)
        if not route and lemma["status"] == "needs_recheck":
            historical_lemmas.setdefault(lemma["proposition"], identifier)

    obligation_nodes = {}
    for route in row["reductions"]:
        route_id = _id("local-lemma", route["implication_id"])
        connect(route_id, root_id, "planned")
        active = route["status"] == "awaiting_obligations"
        route_stale = route["status"] == "needs_recheck"
        obligation_status = "open" if active else route["status"]
        if route_stale:
            obligation_status = "waiting_for_reduction"
        for position, obligation in enumerate(route["obligations"], start=1):
            proposition = obligation["proposition"]
            proved = obligation["lemma_id"]
            if proved:
                connect(_id("local-lemma", proved), route_id, "planned")
                continue
            if route_stale and proposition in historical_lemmas:
                # The recorded proof stays visible without treating it as a current discharge.
                connect(historical_lemmas[proposition], route_id, "planned", relation="stale_candidate")
                continue
            identifier = f"obligation:{route['implication_id']}:{position}"
            nodes[identifier] = {
                "id": identifier, "kind": "obligation", "title": _obligation_title(position),
                "status": obligation_status,
                "formalization": proposition, "proposition": proposition, "parameters": parameters,
                "reference": {"implication_id": route["implication_id"], "position": position},
            }
            if active:
                obligation_nodes.setdefault(proposition, []).append(identifier)
                blockers.append({"node_id": identifier, "kind": "open_obligation", "reason": proposition})
            connect(identifier, route_id, "planned")

    for receipt in row["checks"]:
        target = receipt["target"] or "goal"
        identifier = _id("lean", receipt["id"])
        kind = "refutation" if target == "refutation" else "proof"
        nodes[identifier] = {
            "id": identifier, "kind": "evidence", "title": "Lean " + kind,
            "status": "needs_recheck" if receipt["needs_recheck"] else receipt["review_status"],
            "verification_status": "verified" if receipt["accepted"] else "unverified",
            "formalization": receipt.get("checked_type") or receipt.get("statement_type"),
            "citation": receipt.get("declaration"),
            "reference": {"check_id": receipt["id"], "evidence_id": receipt["evidence_id"],
                          "sha256": receipt["check_sha256"], "module": receipt["source_module"]},
        }
        checked_references(receipt, identifier)
        if receipt["accepted"] is True and target in ("goal", "refutation"):
            connect(identifier, root_id, "actual", relation=kind)

    advance = row["advance"] or {}
    blocked = {"proof_search_blocked", "verification_queue_blocked", "budget_exhausted", "blocked"}
    if advance.get("status") in blocked and row["status"] not in {"proved", "refuted"}:
        blockers.append({"node_id": root_id, "kind": advance["status"],
                         "reason": advance["reason"] or advance["status"]})
        for attempt in advance.get("attempts") or []:
            if not attempt.get("reason"):
                continue
            for identifier in obligation_nodes.get(attempt.get("proposition"), ()):
                blockers.append({"node_id": identifier, "kind": attempt["status"], "reason": attempt["reason"]})

    focus = next((entry["node_id"] for entry in blockers if entry["kind"] != "open_obligation"
                  and entry["node_id"] != root_id), None)
    if not focus:
        focus = blockers[0]["node_id"] if blockers else root_id
    claim = {key: row[key] for key in ("title", "statement", "status", "domain")}
    claim.update(id=str(row["id"]), statement_id=row["lean"]["id"] if row["lean"] else None)
    # #356: what earlier rounds left behind, from the same rows the graph is drawn from.
    ledger = proof_ledger.summarize(row["lemmas"], row["reductions"], advance.get("attempts") or ())
    return {
        "claim": claim, "nodes": list(nodes.values()), "focus_id": focus, "blockers": blockers, "ledger": ledger,
        "edges": [edge for (source, target, _), edge in edges.items() if source in nodes and target in nodes],
    }


_CLAIM_COLUMNS = "claim.id::text AS id, claim.title, effective.status, claim.domain"
_CLAIMS_FROM = """
    FROM rm.claims claim JOIN rm.claim_foundation_status effective ON effective.id = claim.id
"""
_CLAIMS_VISIBLE = """
    WHERE claim.contract_sha256 IS NOT NULL AND claim.kind = 'researcher_claim'
      AND effective.status NOT IN ('superseded', 'archived')
"""
_VISIBLE_CLAIMS = "SELECT " + _CLAIM_COLUMNS + _CLAIMS_FROM + _CLAIMS_VISIBLE
_CLAIM_ORDER = " ORDER BY claim.created_at DESC, claim.id"

# Count recorded route positions before the graph can fold discharged obligations into lemma links.
# Count recorded route positions before the graph can fold discharged obligations into lemma links.
# #342: library items derived from a claim's result, traced back to that claim.
_DERIVED_FROM = """
    SELECT d.version_id, source.claim_id
    FROM rm.formal_lean_derivations d JOIN rm.formalization_packets source ON source.id = d.packet_id
    UNION ALL
    SELECT d.version_id, source.claim_id
    FROM rm.formal_certificate_derivations d JOIN rm.formalization_packets source ON source.id = d.packet_id
"""

# Count recorded route positions before the graph can fold discharged obligations into lemma links.
# #342: also record how a proved claim was established and which claims its current formalization relies on.
# Evidence kinds are returned as recorded; clients name them, so no kind list enters this query.
_CANVAS_CLAIMS = "SELECT " + _CLAIM_COLUMNS + """,
    jsonb_build_object('verified', count.verified, 'total', count.total) AS obligations,
    jsonb_build_object(
        'kind', decisive.kind::text, 'review', decisive.review_status, 'supporting', support.kinds
    ) AS assurance,
    relies.uses
""" + _CLAIMS_FROM + """
    LEFT JOIN LATERAL (""" + _CURRENT_PACKET + """) packet ON true
    LEFT JOIN LATERAL (""" + _CURRENT_STATEMENT + """) statement ON true
    CROSS JOIN LATERAL (
        SELECT count(*) AS total, count(*) FILTER (WHERE obligation.item ->> 'lemma_id' IS NOT NULL) AS verified
        FROM rm.lean_reduction_status(statement.id) route
        CROSS JOIN LATERAL jsonb_array_elements(route.obligations) obligation(item)
    ) count
    LEFT JOIN rm.evidence_foundation_status decisive ON decisive.id = claim.decisive_evidence_id
    CROSS JOIN LATERAL (
        SELECT coalesce(jsonb_agg(DISTINCT e.kind::text ORDER BY e.kind::text), '[]'::jsonb) AS kinds
        FROM rm.evidence e
        WHERE e.claim_id = claim.id AND e.verdict = 'verified' AND e.id IS DISTINCT FROM claim.decisive_evidence_id
    ) support
    -- Items the current formalization relies on, traced to the claim whose result they record: an item
    -- derived from a claim's packet, or a shared theorem published from a claim's decisive evidence.
    CROSS JOIN LATERAL (
        SELECT coalesce(jsonb_agg(DISTINCT source.claim_id::text ORDER BY source.claim_id::text), '[]'::jsonb)
            AS uses
        FROM (
            SELECT b.item_version_id AS version_id FROM rm.get_packet_basis(packet.id) b
            UNION SELECT u.version_id FROM rm.verification_basis_use u
                JOIN rm.lean_check_status c ON c.id = u.check_id
                WHERE c.packet_id = packet.id AND u.dependency_kind = 'actual'
            UNION SELECT u.version_id FROM rm.evidence_basis_use u WHERE u.evidence_id = claim.decisive_evidence_id
        ) used
        JOIN rm.formal_item_status item ON item.id = used.version_id
        LEFT JOIN rm.formalization_status derived ON derived.id = item.derivation_packet_id
        LEFT JOIN rm.current_lean_core_theorems shared ON shared.id = item.id
        LEFT JOIN rm.evidence published ON published.id = shared.evidence_id
        CROSS JOIN LATERAL (SELECT coalesce(derived.claim_id, published.claim_id) AS claim_id) source
        JOIN rm.claim_foundation_status dependency ON dependency.id = source.claim_id
        WHERE source.claim_id <> claim.id AND dependency.status NOT IN ('superseded', 'archived')
    ) relies
""" + _CLAIMS_VISIBLE + _CLAIM_ORDER


def claims(store):
    """List the most recent frozen workspace claims."""
    return store.query("human", _VISIBLE_CLAIMS + _CLAIM_ORDER + " LIMIT 100")


def canvas_claim_index(store):
    """Read visible claims and their current recorded obligation counts, including older claims."""
    return {"claims": store.query("human", _CANVAS_CLAIMS)}


def visible_claim_ids(store, identifiers):
    """Check only the requested claim IDs against the project graph scope."""
    rows = store.query("human", _VISIBLE_CLAIMS + " AND claim.id = ANY(%s::uuid[])", list(identifiers))
    return {row["id"] for row in rows}


def claim_index(store):
    """List recent claims and tell clients whether saved references are available."""
    return {"claims": claims(store), **({"claim_codes": True} if claim_references.available(store) else {})}


def _atlas_node(node):
    result = {"id": node["id"], "kind": node["kind"], "status": node.get("status")}
    title = node.get("title")
    citation = node.get("citation")
    if citation:
        if node["kind"] == "library":
            title = citation
        else:
            result["citation"] = citation
    if title:
        result["title"] = title
    return result


def atlas(store, limit=40):
    """Summarize the recorded nodes and dependencies of recent claims for the workspace overview."""
    overview = []
    for claim in claims(store)[:limit]:
        try:
            graph = snapshot(store, claim["id"], references=False)
        except ValueError:
            continue
        nodes = graph["nodes"]
        overview.append({
            "id": claim["id"], "title": claim["title"], "domain": claim["domain"], "status": claim["status"],
            "nodes": [_atlas_node(node) for node in nodes],
            "edges": [[edge["source"], edge["target"]] for edge in graph["edges"]],
            "awaiting_review": sum(1 for node in nodes if node["kind"] == "evidence"
                                   and node.get("status") in {"pending", "awaiting_review"}),
            "blocked": len({blocker["node_id"] for blocker in graph["blockers"]}),
        })
    return {"claims": overview}


def snapshot(store, claim_id, *, references=True, include_lemma_plan=False):
    """Project the selected frozen packet and its current Lean history in one snapshot."""
    rows = store.query("human", _GRAPH, claim_id)
    if not rows:
        raise ClaimGraphNotFound("No frozen research claim matches this ID.")
    graph = _snapshot(rows[0])
    if include_lemma_plan:
        statement = rows[0]["lean"]
        graph["claim"]["lemma_plan_ready"] = bool(
            statement and statement["check_ready"] and statement["body"].get("theorem") == general_lean.THEOREM
        )
    codes = claim_references.snapshot(store, claim_id) if references else None
    if codes:
        prefix = codes["code"]
        graph["claim"]["code"] = prefix
        for node in graph["nodes"]:
            number = codes["numbers"].get(node["id"])
            if number is not None:
                node["code"] = f"{prefix}-{number:02}"
    return graph


def impact(store, claim_id, basis_version_id):
    """Preview the consequences of withdrawing one frozen hypothesis version."""
    rows = store.query("human", _IMPACT, claim_id, basis_version_id)
    affected, pending = rows[0]["affected"], rows[0]["pending"]
    if not affected:
        raise ValueError("The hypothesis version is outside the selected frozen research claim.")
    claim_id = str(UUID(str(claim_id)))
    scope, recheck = [], []
    visual_kinds = {"lean": "evidence", "item": "library", "local-lemma": "local_lemma", "evidence": "receipt"}
    for row in affected:
        kind, record_id = row["kind"], row["id"]
        names = {key: row[key] for key in ("title", "claim_title") if row.get(key)}
        if kind == "scope":
            scope.append({"id": _id("goal", record_id), "kind": "goal", "claim_id": row["claim_id"], **names})
        else:
            recheck.append({
                "id": _id(kind, record_id), "kind": visual_kinds[kind],
                "claim_id": row["claim_id"], "relation": "direct" if row["direct"] else "indirect",
                "status": "needs_recheck" if row["needs_recheck"] else "would_need_recheck", **names,
            })
    scope.extend({"id": f"obligation:{step['implication_id']}:{step['position']}",
                  "kind": "obligation", "claim_id": claim_id, "title": step["title"]} for step in pending)
    return {"basis_version_id": str(basis_version_id), "affected": recheck, "scope": scope,
            "includes_frozen_scope": True}
