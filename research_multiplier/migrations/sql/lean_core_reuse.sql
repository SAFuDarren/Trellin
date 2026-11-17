-- Bind reusable Core theorems to their exact public, approved Lean receipts.
CREATE TABLE rm.lean_core_exports (
    version_id uuid PRIMARY KEY REFERENCES rm.formal_lean_derivations(version_id),
    evidence_id uuid NOT NULL UNIQUE REFERENCES rm.evidence(id),
    exported_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE rm.lean_core_imports (
    packet_id uuid NOT NULL REFERENCES rm.formalization_approvals(packet_id),
    version_id uuid NOT NULL REFERENCES rm.lean_core_exports(version_id),
    imported_by name NOT NULL DEFAULT session_user,
    imported_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (packet_id, version_id)
);

-- A deterministic receipt can verify the same document reached by several
-- attempts. Store each possible provenance edge without changing the receipt.
CREATE TABLE rm.lean_core_author_provenance (
    evidence_id uuid NOT NULL REFERENCES rm.evidence(id),
    attempt_id uuid NOT NULL REFERENCES rm.attempts(id),
    PRIMARY KEY (evidence_id, attempt_id)
);

CREATE TRIGGER lean_core_exports_immutable BEFORE UPDATE OR DELETE ON rm.lean_core_exports
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();
CREATE TRIGGER lean_core_imports_immutable BEFORE UPDATE OR DELETE ON rm.lean_core_imports
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();
CREATE TRIGGER lean_core_author_provenance_immutable BEFORE UPDATE OR DELETE ON rm.lean_core_author_provenance
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE FUNCTION rm.publish_lean_core_theorem(evidence_id uuid) RETURNS rm.formal_item_versions
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE source record;
DECLARE saved formal_item_versions;
BEGIN
    PERFORM lock_live_foundation();
    SELECT e.id AS eid, e.claim_id, e.contract_sha256, e.code_revision, e.environment_sha256,
           statement.id AS sid, statement.statement_sha256, statement.packet_id, statement.body AS approved,
           f.packet_sha256, c.statement AS natural, checked.id AS check_id, checked.body AS report,
           proof.id AS attempt_id, j.payload AS task
    INTO source
    FROM evidence_foundation_status e
    JOIN claims c ON c.id = e.claim_id AND c.decisive_evidence_id = e.id AND c.status = 'proved'
    JOIN lean_checks checked ON checked.id = e.lean_check_id
    JOIN lean_statement_status statement ON statement.id = checked.statement_id
    JOIN formalization_status f ON f.id = statement.packet_id
    JOIN lean_execution_links execution ON execution.check_id = checked.id AND execution.target = 'goal'
         AND execution.verification_attempt_id = e.verification_attempt_id
    JOIN attempts proof ON proof.id = execution.verification_attempt_id AND proof.role = 'verify'
         AND proof.finding = 'exact_proof'
    JOIN jobs j ON j.id = proof.job_id
    WHERE e.id = publish_lean_core_theorem.evidence_id
        AND c.domain = 'lean-core' AND c.sensitivity = 'public'
        AND e.kind = 'lean' AND e.verdict = 'verified' AND e.diff_status = 'exact'
        AND e.review_status = 'approved' AND NOT e.needs_recheck
        AND statement.check_ready
        AND statement.body ->> 'theorem' = 'RMTheory.expected'
        AND statement.body -> 'checker' = '{"name":"rm-lean-core-audit","version":"1"}'::jsonb
        AND checked.body ->> 'checker_revision' = e.code_revision
        AND checked.body ->> 'verification_environment_sha256' = e.environment_sha256
        AND checked.body ->> 'trusted_source_sha256' = statement.body #>> '{trusted_source,sha256}'
        AND checked.body -> 'submission' = j.payload -> 'submission'
        AND checked.body -> 'candidate' = jsonb_build_object('uri', e.artifact_uri, 'sha256', e.artifact_sha256)
        AND statement.statement_sha256 = j.payload ->> 'statement_sha256';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'only the current promoted exact public Lean Core receipt can be exported'
            USING ERRCODE = '23514';
    END IF;

    saved := insert_formal_item('lean:export:' || source.eid, 'lean',
        jsonb_build_object('kind', 'theorem', 'format', 'lean-core-proof', 'natural', source.natural,
            'formal', jsonb_build_object('lean', jsonb_build_object(
                'proposition', source.approved -> 'theorem_type',
                'antecedents', source.approved #> '{scope,lean_hypotheses}')),
            'source', jsonb_build_object(
                'kind', 'lean', 'evidence_id', source.eid, 'claim_id', source.claim_id,
                'contract_sha256', source.contract_sha256,
                'check_id', source.check_id, 'verification_attempt_id', source.attempt_id,
                'statement_id', source.sid, 'statement_sha256', source.statement_sha256,
                'packet_id', source.packet_id, 'packet_sha256', source.packet_sha256,
                'trusted_source', source.approved -> 'trusted_source',
                'checker', source.approved -> 'checker', 'checker_revision', source.code_revision,
                'environment', source.approved -> 'environment',
                'environment_sha256', source.environment_sha256,
                'candidate', source.report -> 'candidate', 'submission', source.report -> 'submission',
                'basis_version_ids', source.task -> 'basis_version_ids')));
    IF EXISTS (SELECT 1 FROM foundation_stale WHERE kind = 'item' AND id = saved.id) THEN
        RAISE EXCEPTION 'this Lean theorem export has been retired' USING ERRCODE = '23514';
    END IF;
    INSERT INTO formal_lean_derivations (version_id, packet_id, check_id)
        VALUES (saved.id, source.packet_id, source.check_id) ON CONFLICT (version_id) DO NOTHING;
    INSERT INTO lean_core_exports (version_id, evidence_id)
        VALUES (saved.id, source.eid) ON CONFLICT (version_id) DO NOTHING;
    RETURN saved;
END $$;

CREATE FUNCTION rm.capture_promoted_lean_core_theorem() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    PERFORM publish_lean_core_theorem(NEW.decisive_evidence_id);
    RETURN NEW;
END $$;

CREATE TRIGGER capture_promoted_lean_core_theorem AFTER UPDATE OF status ON rm.claims
    FOR EACH ROW WHEN (
        OLD.status IS DISTINCT FROM NEW.status AND NEW.status = 'proved'
        AND NEW.domain = 'lean-core' AND NEW.sensitivity = 'public'
    ) EXECUTE FUNCTION rm.capture_promoted_lean_core_theorem();

CREATE VIEW rm.current_lean_core_theorems WITH (security_barrier = true) AS
    SELECT v.id, v.logical_key, v.version, v.version_sha256, v.body, publication.evidence_id
    FROM rm.lean_core_exports publication
    JOIN rm.formal_item_status v ON v.id = publication.version_id AND v.admitted AND NOT v.needs_recheck
    JOIN rm.evidence_foundation_status e ON e.id = publication.evidence_id AND NOT e.needs_recheck
         AND e.review_status = 'approved'
    JOIN rm.claims c ON c.id = e.claim_id AND c.decisive_evidence_id = e.id AND c.status = 'proved'
    JOIN rm.lean_checks checked ON checked.id = e.lean_check_id
    JOIN rm.lean_statement_status statement ON statement.id = checked.statement_id AND statement.check_ready;

CREATE FUNCTION rm.admit_lean_core_theorem(packet_id uuid, version_id uuid) RETURNS rm.formal_item_versions
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE saved formal_item_versions;
DECLARE target record;
BEGIN
    SELECT f.claim_id, f.packet_sha256 INTO target FROM formalization_status f
    JOIN claims c ON c.id = f.claim_id
    WHERE f.id = admit_lean_core_theorem.packet_id AND f.proof_ready
        AND c.domain = 'lean-core' AND c.sensitivity = 'public';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'a promoted Lean Core theorem needs an approved public Lean Core destination'
            USING ERRCODE = '23514';
    END IF;
    PERFORM require_formalization_ready(target.claim_id, jsonb_build_object(
        'formalization_id', packet_id, 'formalization_sha256', target.packet_sha256));
    PERFORM lock_live_foundation();
    SELECT v.* INTO saved FROM formal_item_versions v
    JOIN current_lean_core_theorems e ON e.id = v.id
    WHERE v.id = admit_lean_core_theorem.version_id
        AND e.body #>> '{source,claim_id}' <> target.claim_id::text;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'the cited theorem needs a current promoted proof from another claim'
            USING ERRCODE = '23514';
    END IF;
    INSERT INTO lean_core_imports (packet_id, version_id)
        VALUES (admit_lean_core_theorem.packet_id, saved.id) ON CONFLICT ON CONSTRAINT lean_core_imports_pkey DO NOTHING;
    RETURN saved;
END $$;

CREATE FUNCTION rm.record_lean_core_author(evidence_id uuid, attempt_id uuid) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM evidence e JOIN lean_checks c ON c.id = e.lean_check_id
        JOIN lean_statements s ON s.id = c.statement_id AND s.body ->> 'theorem' = 'RMTheory.expected'
        JOIN attempts a ON a.id = record_lean_core_author.attempt_id AND a.claim_id = e.claim_id
             AND a.contract_sha256 = e.contract_sha256 AND a.role = 'prove' AND a.finding = 'exact_proof'
             AND a.model_provider IS NOT NULL AND a.artifact_uri = c.body #>> '{submission,uri}'
             AND a.artifact_sha256 = c.body #>> '{submission,sha256}'
        JOIN jobs j ON j.id = a.job_id AND j.payload -> 'lean_author' = 'true'::jsonb
             AND j.payload ->> 'author_statement_id' = s.id::text
             AND j.payload ->> 'author_statement_sha256' = s.statement_sha256
        WHERE e.id = record_lean_core_author.evidence_id AND e.kind = 'lean'
    ) THEN
        RAISE EXCEPTION 'the author attempt did not supply this exact Lean submission' USING ERRCODE = '23514';
    END IF;
    INSERT INTO lean_core_author_provenance (evidence_id, attempt_id)
        VALUES (record_lean_core_author.evidence_id, record_lean_core_author.attempt_id)
        ON CONFLICT ON CONSTRAINT lean_core_author_provenance_pkey DO NOTHING;
    RETURN attempt_id;
END $$;

REVOKE ALL ON FUNCTION rm.publish_lean_core_theorem(uuid), rm.capture_promoted_lean_core_theorem(),
    rm.admit_lean_core_theorem(uuid, uuid), rm.record_lean_core_author(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.publish_lean_core_theorem(uuid), rm.admit_lean_core_theorem(uuid, uuid),
    rm.record_lean_core_author(uuid, uuid) TO rm_verifier;
GRANT SELECT ON rm.current_lean_core_theorems, rm.lean_core_author_provenance TO rm_human, rm_verifier;

CREATE OR REPLACE VIEW rm.foundation_stale WITH (security_barrier = true) AS
    WITH RECURSIVE edges(parent_kind, parent_id, child_kind, child_id) AS (
        SELECT 'item', version_id, 'lean', check_id FROM rm.verification_basis_use
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
        UNION SELECT 'item', publication.version_id FROM rm.lean_core_exports publication
            JOIN rm.evidence receipt ON receipt.id = publication.evidence_id
            JOIN rm.claims c ON c.id = receipt.claim_id
            JOIN rm.lean_checks checked ON checked.id = receipt.lean_check_id
            JOIN rm.lean_statement_status statement ON statement.id = checked.statement_id
            WHERE c.status <> 'proved' OR c.decisive_evidence_id IS DISTINCT FROM receipt.id
               OR NOT statement.check_ready
        UNION SELECT 'item', v.id FROM rm.formal_item_versions v JOIN rm.verification_input_retirements r
            ON r.input_kind = 'source' AND r.sha256 = v.body #>> '{source,sha256}'
        UNION SELECT 'lean', c.id FROM rm.lean_checks c
            JOIN rm.lean_statements s ON s.id = c.statement_id
            JOIN rm.verification_input_retirements r ON
                (r.input_kind = 'verifier' AND r.sha256 = coalesce(c.body ->> 'checker_revision',
                    s.body #>> '{environment,inputs,RMFlow/Audit.lean}')) OR
                (r.input_kind = 'environment' AND r.sha256 IN (c.body ->> 'environment_sha256',
                                                              c.body ->> 'verification_environment_sha256')) OR
                (r.input_kind = 'source' AND r.sha256 IN (s.body #>> '{trusted_source,sha256}',
                    c.body #>> '{candidate,sha256}', c.body #>> '{submission,sha256}'))
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

CREATE OR REPLACE FUNCTION rm.get_packet_basis(packet_id uuid)
RETURNS TABLE (item_version_id uuid, logical_key text, version integer, version_sha256 text,
               body jsonb, packet_label text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT item.id, item.logical_key, item.version, item.version_sha256::text, item.body, packet_item.item_label
    FROM formalization_status s
    CROSS JOIN LATERAL (
        SELECT version_id FROM packet_basis_items b WHERE b.packet_id = s.id
        UNION SELECT version_id FROM packet_library_items l WHERE l.packet_id = s.id
        UNION SELECT version_id FROM formal_lean_derivations d WHERE d.packet_id = s.id
        UNION SELECT version_id FROM formal_certificate_derivations d WHERE d.packet_id = s.id
        UNION SELECT i.version_id FROM lean_core_imports i JOIN current_lean_core_theorems active ON active.id = i.version_id
            WHERE i.packet_id = s.id
    ) attached
    JOIN formal_item_status item ON item.id = attached.version_id AND item.admitted AND NOT item.needs_recheck
    LEFT JOIN packet_basis_items packet_item ON packet_item.packet_id = s.id AND packet_item.version_id = item.id
    WHERE s.id = $1 AND s.proof_ready
    ORDER BY item.logical_key, item.version
$$;

