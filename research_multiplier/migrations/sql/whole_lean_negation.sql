-- Negate the complete verified implication, including its frozen antecedents.
-- Match the byte layout of the Lean source generator.
CREATE OR REPLACE FUNCTION rm.lean_negation_sha(approved jsonb) RETURNS text
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT encode(sha256(convert_to(
        'import Lean' || chr(10) || coalesce((
            SELECT string_agg('import ' || m.value || chr(10), '' ORDER BY m.ordinal)
            FROM jsonb_array_elements_text(approved #> '{environment,external,modules}')
                 WITH ORDINALITY m(value, ordinal)
        ), '') || 'set_option autoImplicit false' || chr(10) || 'set_option maxHeartbeats 200000' || chr(10)
        || 'def RMTheory.expected : Prop := (' || chr(10) || '_root_.Not (' || chr(10)
        || (SELECT string_agg('(' || chr(10) || a.value || chr(10) || ')', ' → ' ORDER BY a.ordinal)
            FROM jsonb_array_elements_text(approved #> '{scope,lean_hypotheses}'
                || jsonb_build_array(approved ->> 'theorem_type')) WITH ORDINALITY a(value, ordinal))
        || chr(10) || ')' || chr(10) || ')' || chr(10), 'UTF8')), 'hex');
$$;

-- Refutations of just the consequent remain visible in history. The same
-- checked type can still be valid as an ordinary conditional lemma.
CREATE VIEW rm.superseded_lean_refutations WITH (security_barrier = true) AS
    SELECT checked.id AS check_id
    FROM rm.lean_execution_links execution
    JOIN rm.lean_checks checked ON checked.id = execution.check_id
    JOIN rm.lean_statements approved ON approved.id = checked.statement_id
    WHERE execution.target = 'refutation'
      AND checked.body ->> 'refutation_source_sha256' IS DISTINCT FROM rm.lean_negation_sha(approved.body);

GRANT EXECUTE ON FUNCTION rm.lean_negation_sha(jsonb) TO rm_human, rm_scheduler, rm_verifier, rm_agent;

CREATE VIEW rm.retired_lean_checks WITH (security_barrier = true) AS
    SELECT DISTINCT c.id
    FROM rm.lean_checks c
    JOIN rm.lean_statements s ON s.id = c.statement_id
    JOIN rm.verification_input_retirements r ON
        (r.input_kind = 'verifier' AND r.sha256 IN (coalesce(c.body ->> 'checker_revision',
            s.body #>> '{environment,inputs,RMFlow/Audit.lean}'), c.body ->> 'local_checker_revision')) OR
        (r.input_kind = 'environment' AND r.sha256 IN (c.body ->> 'environment_sha256',
                                                      c.body ->> 'verification_environment_sha256')) OR
        (r.input_kind = 'source' AND r.sha256 IN (s.body #>> '{trusted_source,sha256}',
            c.body #>> '{candidate,sha256}', c.body #>> '{submission,sha256}',
            c.body ->> 'refutation_source_sha256'));

CREATE OR REPLACE VIEW rm.foundation_stale WITH (security_barrier = true) AS
    WITH RECURSIVE edges(parent_kind, parent_id, child_kind, child_id) AS (
        SELECT 'item', version_id, 'lean', check_id FROM rm.verification_basis_use
        UNION ALL SELECT 'local-lemma', lemma_id, 'lean', check_id FROM rm.verification_local_lemma_use
        UNION ALL SELECT 'local-lemma', prerequisite_id, 'local-lemma', lemma_id FROM rm.local_lean_lemma_dependencies
        UNION ALL SELECT 'item', version_id, 'local-lemma', lemma_id FROM rm.local_lean_lemma_basis
        UNION ALL SELECT 'item', scoped.version_id, 'local-lemma', l.id FROM rm.local_lean_lemmas l
            JOIN rm.lean_statements statement ON statement.id = l.statement_id
            JOIN rm.packet_basis_items scoped ON scoped.packet_id = statement.packet_id
        UNION ALL SELECT 'item', basis.version_id, 'lean', legacy.id FROM rm.lean_checks legacy
            JOIN rm.lean_statements statement ON statement.id = legacy.statement_id
            JOIN rm.packet_basis_items basis ON basis.packet_id = statement.packet_id
            WHERE NOT EXISTS (SELECT 1 FROM rm.lean_execution_links execution WHERE execution.check_id = legacy.id)
        UNION ALL SELECT 'item', version_id, 'evidence', evidence_id FROM rm.evidence_basis_use
        UNION ALL SELECT 'lean', check_id, 'item', version_id FROM rm.formal_lean_derivations
        UNION ALL SELECT 'evidence', evidence_id, 'item', version_id FROM rm.lean_core_exports
        UNION ALL SELECT 'lean', lean_check_id, 'evidence', id FROM rm.evidence WHERE lean_check_id IS NOT NULL
        UNION ALL SELECT 'item', basis.version_id, 'item', bridge.version_id
            FROM rm.formal_certificate_derivations bridge JOIN rm.flow_derivation_records fact
                ON fact.id = bridge.derivation_id
            JOIN rm.packet_basis_items basis ON basis.packet_id = fact.packet_id
                AND basis.item_label = ANY(fact.premise_item_ids)
        UNION ALL SELECT 'evidence', e.id, 'item', bridge.version_id
            FROM rm.formal_certificate_derivations bridge JOIN rm.flow_derivation_records fact
                ON fact.id = bridge.derivation_id
            JOIN rm.evidence e ON e.verification_attempt_id = fact.verification_attempt_id
                AND e.kind = 'exact_certificate' AND e.verdict = 'verified'
    ), roots(kind, id) AS (
        SELECT 'item', version_id FROM rm.formal_item_retirements
        UNION SELECT 'lean', check_id FROM rm.superseded_lean_refutations
        UNION SELECT 'local-lemma', l.id FROM rm.local_lean_lemmas l
            JOIN rm.lean_statements statement ON statement.id = l.statement_id
            JOIN rm.formalization_status packet ON packet.id = statement.packet_id
            WHERE NOT packet.proof_ready OR NOT rm.local_lean_lemma_source_live(l.id)
        UNION SELECT 'item', exported.version_id FROM rm.lean_core_exports exported
            WHERE (SELECT review.action FROM rm.decisions review WHERE review.evidence_id = exported.evidence_id
                AND review.action IN ('approve','return') ORDER BY review.sequence_no DESC LIMIT 1) = 'return'
        UNION SELECT 'item', derived.version_id FROM rm.formal_lean_derivations derived
            WHERE (SELECT review.action FROM rm.lean_check_reviews review WHERE review.check_id = derived.check_id
                ORDER BY review.sequence_no DESC LIMIT 1) = 'return'
        UNION SELECT 'item', publication.version_id FROM rm.lean_core_exports publication
            JOIN rm.evidence receipt ON receipt.id = publication.evidence_id
            JOIN rm.claims c ON c.id = receipt.claim_id
            JOIN rm.lean_checks checked ON checked.id = receipt.lean_check_id
            JOIN rm.lean_statement_status statement ON statement.id = checked.statement_id
            WHERE c.status <> 'proved' OR c.decisive_evidence_id IS DISTINCT FROM receipt.id
               OR NOT statement.check_ready
        UNION SELECT 'item', v.id FROM rm.formal_item_versions v JOIN rm.verification_input_retirements r
            ON r.input_kind = 'source' AND r.sha256 = v.body #>> '{source,sha256}'
        UNION SELECT 'lean', retired.id FROM rm.retired_lean_checks retired
        UNION SELECT 'lean', c.id FROM rm.lean_checks c JOIN rm.lean_statements s ON s.id = c.statement_id
            WHERE EXISTS (SELECT 1 FROM rm.lean_statements newer WHERE newer.packet_id = s.packet_id
                          AND newer.version > s.version)
        UNION SELECT 'item', bridge.version_id FROM rm.formal_certificate_derivations bridge
            JOIN rm.flow_derivation_records fact ON fact.id = bridge.derivation_id
            JOIN rm.attempts attempt ON attempt.id = fact.verification_attempt_id
            JOIN rm.jobs job ON job.id = attempt.job_id
            JOIN rm.verification_input_retirements r ON r.input_kind = 'verifier'
                AND r.sha256 = job.payload ->> 'checker_revision'
        UNION SELECT 'evidence', e.id FROM rm.evidence e JOIN rm.verification_input_retirements r
            ON (r.input_kind = 'verifier' AND r.sha256 = e.code_revision)
            OR (r.input_kind = 'environment' AND r.sha256 = e.environment_sha256)
            OR (r.input_kind = 'source' AND r.sha256 = e.artifact_sha256)
    ), obsolete(kind, id) AS (
        SELECT kind, id FROM roots
        UNION
        SELECT e.child_kind, e.child_id FROM obsolete o JOIN edges e
            ON e.parent_kind = o.kind AND e.parent_id = o.id
    )
    SELECT kind, id FROM obsolete;

-- A pending old job cannot satisfy the new source binding when leased again.
DO $$
DECLARE obsolete record;
BEGIN
    FOR obsolete IN
        SELECT job.id FROM rm.jobs job
        JOIN rm.lean_statements approved ON approved.id::text = job.payload ->> 'lean_statement_id'
        WHERE job.kind = 'verify' AND job.status IN ('queued', 'running', 'failed')
          AND job.payload ->> 'target' = 'refutation'
          AND job.payload ->> 'refutation_source_sha256' IS DISTINCT FROM rm.lean_negation_sha(approved.body)
        FOR UPDATE OF job
    LOOP
        PERFORM rm.cancel_research_job(obsolete.id, 'schema migration 0035',
            'Recompile the refutation against the negation of the whole frozen proposition.');
    END LOOP;
END $$;

CREATE TABLE rm.claim_evidence_revisions (
    claim_id uuid PRIMARY KEY REFERENCES rm.claims(id),
    revision bigint NOT NULL DEFAULT 1
);

-- A real row revision makes a stale repeatable-read transaction retry. Under
-- read committed, checks after this fence see the preceding writer's receipts.
CREATE FUNCTION rm.advance_claim_evidence_revision(subject_id uuid) RETURNS void
LANGUAGE sql SET search_path = pg_catalog, rm, pg_temp AS $$
    INSERT INTO claim_evidence_revisions (claim_id) VALUES ($1)
    ON CONFLICT (claim_id) DO UPDATE
        SET revision = claim_evidence_revisions.revision + 1
$$;

CREATE FUNCTION rm.guard_conflicting_receipt() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    PERFORM advance_claim_evidence_revision(NEW.claim_id);
    IF EXISTS (
        SELECT 1 FROM claims claim JOIN evidence_foundation_status decisive ON decisive.id = claim.decisive_evidence_id
        WHERE claim.id = NEW.claim_id AND claim.contract_sha256 = NEW.contract_sha256
          AND NOT decisive.needs_recheck AND decisive.review_status <> 'returned'
          AND (decisive.kind = 'counterexample') <> (NEW.kind = 'counterexample')
    ) THEN
        RAISE EXCEPTION 'the verified receipt conflicts with the current decisive evidence; review the verifier attempt'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER guard_conflicting_receipt AFTER INSERT ON rm.evidence
    FOR EACH ROW WHEN (NEW.verdict = 'verified' AND NEW.diff_status = 'exact'
        AND NEW.kind IN ('lean', 'exact_certificate', 'audited_proof', 'counterexample'))
    EXECUTE FUNCTION rm.guard_conflicting_receipt();

CREATE FUNCTION rm.guard_conflicting_promotion() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    PERFORM advance_claim_evidence_revision(NEW.id);
    IF EXISTS (
        SELECT 1 FROM evidence other
        JOIN evidence_foundation_status current ON current.id = other.id
        WHERE other.claim_id = NEW.id AND other.contract_sha256 = NEW.contract_sha256
          AND other.verdict = 'verified' AND other.diff_status = 'exact'
          AND other.kind IN ('lean', 'exact_certificate', 'audited_proof', 'counterexample')
          AND (other.kind = 'counterexample') <> (NEW.status = 'refuted')
          AND current.review_status <> 'returned' AND can_promote_receipt(other)
    ) THEN
        RAISE EXCEPTION 'conflicting exact evidence must be resolved before promoting the claim'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER guard_conflicting_promotion BEFORE UPDATE OF status ON rm.claims
    FOR EACH ROW WHEN (OLD.status = 'open' AND NEW.status IN ('proved', 'refuted'))
    EXECUTE FUNCTION rm.guard_conflicting_promotion();

-- A receipt that is issuing may hold the revision row before PostgreSQL checks
-- its claim foreign key. Let that key-share lock coexist with promotion.
CREATE OR REPLACE FUNCTION rm.promote_claim(evidence_id uuid, reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE receipt evidence;
BEGIN
    SELECT * INTO STRICT receipt FROM evidence WHERE id = promote_claim.evidence_id;
    PERFORM 1 FROM claims WHERE id = receipt.claim_id AND status = 'open' FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'promotion requires an open claim' USING ERRCODE = '23514';
    END IF;
    PERFORM require_formalization_ready(receipt.claim_id);
    PERFORM lock_live_foundation();
    IF NOT can_promote_receipt(receipt) THEN
        RAISE EXCEPTION 'promotion requires a current verified receipt' USING ERRCODE = '23514';
    END IF;
    UPDATE claims SET status = CASE WHEN receipt.kind = 'counterexample' THEN 'refuted'::claim_status
                                    ELSE 'proved'::claim_status END,
        decisive_evidence_id = receipt.id WHERE id = receipt.claim_id;
    INSERT INTO decisions (claim_id, contract_sha256, evidence_id, action, decided_by, actor_ref, reason)
        VALUES (receipt.claim_id, receipt.contract_sha256, receipt.id, 'promote', 'program', session_user, reason);
    RETURN receipt.claim_id;
END $$;

REVOKE ALL ON FUNCTION rm.advance_claim_evidence_revision(uuid), rm.guard_conflicting_receipt(),
    rm.guard_conflicting_promotion() FROM PUBLIC;
