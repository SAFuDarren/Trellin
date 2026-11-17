-- The confirmed statement keeps its original byte-for-byte environment
-- binding. A native Lake check also names the exact runtime it measured.
CREATE TABLE rm.lean_lake_baselines (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    statement_id uuid NOT NULL,
    statement_sha256 rm.sha256_hex NOT NULL,
    original_environment_sha256 rm.sha256_hex NOT NULL,
    scope_revision rm.sha256_hex NOT NULL,
    approved_declarations jsonb NOT NULL CHECK (jsonb_typeof(approved_declarations) = 'array'),
    approved_declarations_sha256 rm.sha256_hex NOT NULL,
    snapshot jsonb NOT NULL CHECK (jsonb_typeof(snapshot) = 'object'),
    snapshot_sha256 rm.sha256_hex NOT NULL,
    recorded_by name NOT NULL DEFAULT session_user,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (statement_id, statement_sha256) REFERENCES rm.lean_statements(id, statement_sha256),
    CONSTRAINT lean_lake_baselines_scope_key UNIQUE (statement_id, scope_revision, approved_declarations_sha256)
);
CREATE TRIGGER lean_lake_baselines_append_only BEFORE UPDATE OR DELETE ON rm.lean_lake_baselines
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();
REVOKE ALL ON rm.lean_lake_baselines FROM PUBLIC;

CREATE FUNCTION rm.order_lean_lake_basis(items jsonb) RETURNS jsonb
LANGUAGE sql IMMUTABLE STRICT SET search_path = pg_catalog AS $$
    SELECT coalesce(jsonb_agg(entry ORDER BY (entry ->> 'declaration') COLLATE "C",
        entry ->> 'version_id'), '[]'::jsonb)
    FROM jsonb_array_elements(items) AS basis(entry)
$$;
REVOKE ALL ON FUNCTION rm.order_lean_lake_basis(jsonb) FROM PUBLIC;

CREATE FUNCTION rm.current_lean_lake_basis(packet_id uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT order_lean_lake_basis(coalesce(jsonb_agg(jsonb_build_object(
        'version_id', item.item_version_id, 'declaration', item.body #>> '{formal,lean,declaration}')), '[]'::jsonb))
    FROM get_packet_basis($1) item
    WHERE item.body ->> 'kind' = 'theorem'
        AND jsonb_typeof(item.body #> '{formal,lean,declaration}') = 'string'
$$;
REVOKE ALL ON FUNCTION rm.current_lean_lake_basis(uuid) FROM PUBLIC;

CREATE FUNCTION rm.get_lean_lake_baseline(statement_id uuid, scope_revision text, approved_declarations jsonb)
RETURNS SETOF rm.lean_lake_baselines
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    WITH requested AS (SELECT order_lean_lake_basis($3) AS basis)
    SELECT saved.* FROM lean_lake_baselines saved CROSS JOIN requested
    WHERE saved.statement_id = $1 AND saved.scope_revision = $2
      AND saved.approved_declarations_sha256 = encode(sha256(convert_to(requested.basis::text, 'UTF8')), 'hex')
      AND saved.approved_declarations = requested.basis
$$;
REVOKE ALL ON FUNCTION rm.get_lean_lake_baseline(uuid,text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.get_lean_lake_baseline(uuid,text,jsonb) TO rm_verifier, rm_human;

CREATE FUNCTION rm.record_lean_lake_baseline(statement_id uuid, statement_sha256 text,
    original_environment_sha256 text, scope_revision text, approved_declarations jsonb, snapshot jsonb)
RETURNS rm.lean_lake_baselines
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE anchor lean_statements;
DECLARE expected jsonb;
DECLARE mapped jsonb;
DECLARE normalized jsonb;
DECLARE saved lean_lake_baselines;
BEGIN
    -- Serialize against new proposals and changes to the approved basis.
    PERFORM 1 FROM formalization_reviews r JOIN formalization_packets p ON p.review_id = r.id
    JOIN lean_statements s ON s.packet_id = p.id WHERE s.id = $1 FOR SHARE OF r;
    PERFORM lock_live_foundation();
    -- A proposal may measure its exact artifacts before its ordinary human
    -- confirmation. Scheduling a job still requires that confirmation.
    anchor := require_lean_review($1, false);
    IF anchor.statement_sha256 IS DISTINCT FROM $2
        OR anchor.body ->> 'theorem' IS DISTINCT FROM 'RMTheory.expected'
        OR jsonb_typeof(anchor.body #> '{environment,external,modules}') IS DISTINCT FROM 'array'
        OR anchor.body #> '{environment,external,modules}' = '[]'::jsonb
        OR anchor.body #>> '{environment,sha256}' IS DISTINCT FROM $3
        OR NOT coalesce($4 ~ '^[0-9a-f]{64}$', false) THEN
        RAISE EXCEPTION 'a Lake baseline requires the exact original statement and environment'
            USING ERRCODE = '23514';
    END IF;
    IF jsonb_typeof($5) IS DISTINCT FROM 'array'
        OR jsonb_typeof($6) IS DISTINCT FROM 'object'
        OR NOT $6 ?& ARRAY['goal','foundation','basis']
        OR $6 - ARRAY['goal','foundation','basis'] <> '{}'::jsonb
        OR jsonb_typeof($6 -> 'goal') IS DISTINCT FROM 'string'
        OR jsonb_typeof($6 -> 'foundation') IS DISTINCT FROM 'string'
        OR NOT coalesce($6 ->> 'goal' ~ '^[0-9a-f]{64}$', false)
        OR NOT coalesce($6 ->> 'foundation' ~ '^[0-9a-f]{64}$', false)
        OR jsonb_typeof($6 -> 'basis') IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'a Lake baseline needs the goal, foundation and exact basis digests'
            USING ERRCODE = '23514';
    END IF;
    expected := current_lean_lake_basis(anchor.packet_id);
    IF order_lean_lake_basis($5) IS DISTINCT FROM expected THEN
        RAISE EXCEPTION 'a Lake baseline must include the exact current reviewed declaration map'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT FROM jsonb_array_elements($6 -> 'basis') AS basis(entry)
        WHERE CASE WHEN jsonb_typeof(entry) = 'object' THEN
            NOT entry ?& ARRAY['version_id','declaration','sha256']
            OR entry - ARRAY['version_id','declaration','sha256'] <> '{}'::jsonb
            OR jsonb_typeof(entry -> 'sha256') IS DISTINCT FROM 'string'
            OR NOT coalesce(entry ->> 'sha256' ~ '^[0-9a-f]{64}$', false)
        ELSE true END
    ) THEN
        RAISE EXCEPTION 'a Lake baseline must bind each reviewed declaration to a semantic digest'
            USING ERRCODE = '23514';
    END IF;
    SELECT order_lean_lake_basis(coalesce(jsonb_agg(entry - 'sha256'), '[]'::jsonb))
    INTO mapped FROM jsonb_array_elements($6 -> 'basis') AS basis(entry);
    IF mapped IS DISTINCT FROM expected THEN
        RAISE EXCEPTION 'Lake baseline digests must cover every reviewed declaration exactly once'
            USING ERRCODE = '23514';
    END IF;
    normalized := jsonb_set($6, '{basis}', order_lean_lake_basis($6 -> 'basis'));
    INSERT INTO lean_lake_baselines (statement_id, statement_sha256, original_environment_sha256,
        scope_revision, approved_declarations, approved_declarations_sha256, snapshot, snapshot_sha256)
    VALUES ($1, $2, $3, $4, expected, encode(sha256(convert_to(expected::text, 'UTF8')), 'hex'),
        normalized, encode(sha256(convert_to(normalized::text, 'UTF8')), 'hex'))
    ON CONFLICT ON CONSTRAINT lean_lake_baselines_scope_key DO NOTHING
    RETURNING * INTO saved;
    IF NOT FOUND THEN
        SELECT * INTO STRICT saved FROM get_lean_lake_baseline($1, $4, expected);
        IF saved.snapshot IS DISTINCT FROM normalized THEN
            RAISE EXCEPTION 'the original Lake baseline is already recorded with different semantics'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN saved;
END $$;
REVOKE ALL ON FUNCTION rm.record_lean_lake_baseline(uuid,text,text,text,jsonb,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.record_lean_lake_baseline(uuid,text,text,text,jsonb,jsonb) TO rm_verifier;

ALTER FUNCTION rm.require_dynamic_lean_job(rm.jobs) RENAME TO require_prior_lake_lean_job;
CREATE FUNCTION rm.require_dynamic_lean_job(job rm.jobs) RETURNS rm.lean_statements
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE anchor lean_statements;
DECLARE validation jsonb := job.payload -> 'lake_validation';
BEGIN
    anchor := require_prior_lake_lean_job(job);
    IF NOT (job.payload ? 'lake_validation') THEN
        RETURN anchor;
    END IF;
    IF NOT job.payload ? 'lean_source'
        OR jsonb_typeof(anchor.body #> '{environment,external,modules}') IS DISTINCT FROM 'array'
        OR anchor.body #> '{environment,external,modules}' = '[]'::jsonb
        OR jsonb_typeof(validation) IS DISTINCT FROM 'object'
        OR NOT validation ?& ARRAY['baseline_id','scope_revision','snapshot_sha256','runtime_sha256']
        OR validation - ARRAY['baseline_id','scope_revision','snapshot_sha256','runtime_sha256'] <> '{}'::jsonb
        OR jsonb_typeof(validation -> 'runtime_sha256') IS DISTINCT FROM 'string'
        OR NOT coalesce(validation ->> 'runtime_sha256' ~ '^[0-9a-f]{64}$', false)
        OR NOT EXISTS (
            SELECT FROM get_lean_lake_baseline(anchor.id, validation ->> 'scope_revision',
                job.payload #> '{lean_source,approved_declarations}') saved
            WHERE to_jsonb(saved.id) = validation -> 'baseline_id'
                AND to_jsonb(saved.scope_revision) = validation -> 'scope_revision'
                AND to_jsonb(saved.snapshot_sha256) = validation -> 'snapshot_sha256'
                AND saved.statement_sha256 = anchor.statement_sha256
                AND saved.original_environment_sha256 = job.payload ->> 'verification_environment_sha256'
                AND saved.approved_declarations = current_lean_lake_basis(anchor.packet_id)
        ) THEN
        RAISE EXCEPTION 'a Lake verification job must bind the current runtime to its original semantic baseline'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT FROM verification_input_retirements retired
        WHERE (retired.input_kind, retired.sha256::text) IN (
            ('verifier', validation ->> 'scope_revision'), ('environment', validation ->> 'runtime_sha256')
        )
    ) THEN
        RAISE EXCEPTION 'the observed Lake verification environment requires recheck' USING ERRCODE = '23514';
    END IF;
    RETURN anchor;
END $$;
REVOKE ALL ON FUNCTION rm.require_dynamic_lean_job(rm.jobs) FROM PUBLIC;

-- Newly inserted jobs use the baseline protocol. Historical job rows and
-- receipts retain their original byte-pinned semantics.
CREATE FUNCTION rm.guard_new_lean_lake_job() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF NEW.payload ? 'lake_validation' AND (
        NEW.kind <> 'verify' OR NOT NEW.payload ?& ARRAY['lean_source','lean_statement_id']
    ) THEN
        RAISE EXCEPTION 'Lake validation belongs to a native Lean verification job' USING ERRCODE = '23514';
    END IF;
    IF NOT NEW.payload ? 'lake_validation' AND EXISTS (
        SELECT FROM lean_statements s WHERE s.id::text = NEW.payload ->> 'lean_statement_id'
            AND jsonb_typeof(s.body #> '{environment,external,modules}') = 'array'
            AND s.body #> '{environment,external,modules}' <> '[]'::jsonb
    ) THEN
        RAISE EXCEPTION 'a new native Lake check requires a semantic baseline and observed runtime'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER guard_new_lean_lake_job BEFORE INSERT ON rm.jobs
    FOR EACH ROW WHEN (NEW.payload ?| ARRAY['lean_source','lake_validation'])
    EXECUTE FUNCTION rm.guard_new_lean_lake_job();
REVOKE ALL ON FUNCTION rm.guard_new_lean_lake_job() FROM PUBLIC;

CREATE FUNCTION rm.guard_lean_lake_receipt() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE validation jsonb;
BEGIN
    IF NEW.body ? 'verification_attempt_id' THEN
        SELECT j.payload -> 'lake_validation' INTO validation FROM attempts a JOIN jobs j ON j.id = a.job_id
        WHERE a.id = (NEW.body ->> 'verification_attempt_id')::uuid;
    END IF;
    IF NEW.body -> 'lake_validation' IS DISTINCT FROM validation THEN
        RAISE EXCEPTION 'Lake receipt metadata must match the successful verification job'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER guard_lean_lake_receipt BEFORE INSERT ON rm.lean_checks
    FOR EACH ROW EXECUTE FUNCTION rm.guard_lean_lake_receipt();
REVOKE ALL ON FUNCTION rm.guard_lean_lake_receipt() FROM PUBLIC;

CREATE OR REPLACE VIEW rm.retired_lean_checks WITH (security_barrier = true) AS
    SELECT DISTINCT c.id
    FROM rm.lean_checks c
    JOIN rm.lean_statements s ON s.id = c.statement_id
    JOIN rm.verification_input_retirements r ON
        (r.input_kind = 'verifier' AND r.sha256 IN (coalesce(c.body ->> 'checker_revision',
            s.body #>> '{environment,inputs,RMFlow/Audit.lean}'), c.body ->> 'local_checker_revision',
            c.body #>> '{lean_source,verifier_revision}', c.body #>> '{lake_validation,scope_revision}')) OR
        (r.input_kind = 'environment' AND r.sha256 IN (c.body ->> 'environment_sha256',
            c.body ->> 'verification_environment_sha256', c.body #>> '{lake_validation,runtime_sha256}')) OR
        (r.input_kind = 'source' AND r.sha256 IN (s.body #>> '{trusted_source,sha256}',
            c.body #>> '{candidate,sha256}', c.body #>> '{submission,sha256}',
            c.body ->> 'refutation_source_sha256', c.body #>> '{report,sha256}'));
