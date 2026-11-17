"""Generate shareable evidence metadata from scoped, allowlisted database fields."""

from ..model_adapters import PROVIDERS
from research_multiplier.protocol.serialization import fingerprint


def export(store, evidence_id):
    """Read a consistent evidence and decision snapshot without opening private artifacts."""
    rows = store.query("human", """
        SELECT
            jsonb_build_object(
                'claim_id', e.claim_id, 'contract_sha256', e.contract_sha256,
                'evidence_id', e.id, 'grade', e.kind, 'verdict', e.verdict, 'scope', e.diff_status,
                'review_status', e.review_status, 'needs_recheck', e.needs_recheck,
                'audit_mode', e.audit_mode, 'artifact_sha256', e.artifact_sha256,
                'environment_sha256', e.environment_sha256, 'issuance_sha256', e.issuance_sha256,
                'checker_revision', CASE WHEN e.code_revision ~ '^(?:[a-f0-9]{40}|[a-f0-9]{64})$'
                    THEN e.code_revision END,
                'lean_check_id', e.lean_check_id,
                'issued_at', to_char(e.issued_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
            ) AS evidence,
            coalesce((
                SELECT jsonb_agg(jsonb_build_object(
                    'id', a.id, 'role', a.role, 'finding', a.finding, 'model_used', a.model_id IS NOT NULL,
                    'artifact_sha256', a.artifact_sha256, 'prompt_sha256', a.prompt_sha256,
                    'provider', CASE WHEN a.model_provider = ANY(%s) THEN a.model_provider END,
                    'input_tokens', a.input_tokens, 'output_tokens', a.output_tokens
                ) ORDER BY a.submitted_at, a.id)
                FROM rm.attempts a
                WHERE a.id IN (e.attempt_id, e.audit_attempt_id, e.verification_attempt_id)
                    OR a.id IN (SELECT p.attempt_id FROM rm.lean_core_author_provenance p WHERE p.evidence_id = e.id)
                    OR EXISTS (SELECT 1 FROM rm.lean_search_provenance p
                               WHERE p.evidence_id = e.id AND a.id = ANY(p.proposal_attempt_ids))
                    OR a.id IN (SELECT rm.lean_dependency_proposals(e.lean_check_id))
            ), '[]'::jsonb) AS attempts,
            coalesce((
                SELECT jsonb_agg(jsonb_build_object(
                    'version_id', basis.id, 'sha256', basis.version_sha256,
                    'use', CASE WHEN e.lean_check_id IS NULL THEN 'recorded'
                        WHEN EXISTS (SELECT FROM rm.verification_basis_use v WHERE v.check_id = e.lean_check_id
                            AND v.version_id = basis.id AND v.dependency_kind = 'actual') THEN 'actual'
                        ELSE 'scope' END
                ) ORDER BY basis.id)
                FROM rm.evidence_basis_use link
                JOIN rm.formal_item_status basis ON basis.id = link.version_id
                WHERE link.evidence_id = e.id
            ), '[]'::jsonb) AS basis,
            coalesce((
                SELECT jsonb_agg(jsonb_build_object(
                    'action', d.action, 'role', d.decided_by,
                    'recorded_at', to_char(d.decided_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
                ) ORDER BY d.sequence_no)
                FROM rm.decisions d WHERE d.evidence_id = e.id
            ), '[]'::jsonb) AS decisions,
            coalesce((
                SELECT jsonb_agg(jsonb_build_object('id', x.id, 'proof_sha256', x.proof_sha256,
                    'report_sha256', x.report_sha256) ORDER BY x.recorded_at, x.id)
                FROM rm.proof_scope_extractions x WHERE x.evidence_id = e.id
            ), '[]'::jsonb) AS blind_scope_extractions,
            (SELECT jsonb_build_object(
                'review_id', n.review_id, 'assessment', n.assessment, 'status', n.status,
                'review_evidence_id', n.evidence_id,
                'searches', coalesce((
                    SELECT jsonb_agg(jsonb_build_object(
                        'id', s.id, 'provider', s.body ->> 'provider', 'response_sha256', s.body #>> '{response,sha256}'
                    ) ORDER BY s.id)
                    FROM rm.novelty_review_searches link
                    JOIN rm.literature_searches s ON s.id = link.search_id
                    WHERE link.review_id = n.review_id
                ), '[]'::jsonb),
                'originals', coalesce((
                    SELECT jsonb_agg(jsonb_build_object(
                        'id', original.id, 'source_sha256', original.source_sha256,
                        'pdf_sha256', original.body #>> '{archive,sha256}',
                        'linked_hits', coalesce((
                            SELECT jsonb_agg(jsonb_build_object('search_id', l.search_id, 'rank', l.rank)
                                ORDER BY l.search_id, l.rank)
                            FROM rm.novelty_review_originals l
                            WHERE l.review_id = n.review_id AND l.source_id = original.id
                        ), '[]'::jsonb)
                    ) ORDER BY original.id)
                    FROM rm.literature_source_status original WHERE original.id = ANY(n.source_ids)
                ), '[]'::jsonb)
            ) FROM rm.novelty_review_status n WHERE n.claim_id = e.claim_id) AS literature
        FROM rm.evidence_foundation_status e WHERE e.id = %s
    """, sorted(PROVIDERS), evidence_id)
    if not rows:
        raise ValueError("No evidence matches this disclosure request.")
    provenance = rows[0]
    model_roles = {}
    for attempt in provenance["attempts"]:
        if attempt["model_used"]:
            role = attempt["role"]
            model_roles[role] = model_roles.get(role, 0) + 1
    document = {
        "schema_version": 1,
        "provenance": provenance,
        "ai_use": {
            "coverage": "Recorded model attempts linked to this evidence or its checked local dependencies, and recorded blind scope extractions.",
            "model_attempts_by_role": model_roles,
            "blind_scope_extraction_count": len(provenance["blind_scope_extractions"]),
            "human_approval": provenance["evidence"]["review_status"] == "approved",
        },
    }
    return {**document, "sha256": fingerprint(document)}
