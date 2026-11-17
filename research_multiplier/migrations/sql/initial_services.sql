-- Runtime group roles are cluster-local capability groups. Logins are provisioned
-- outside migrations. Reuse groups safely when several workspaces share a cluster.
DO $$
DECLARE group_name text;
BEGIN
    FOR group_name IN SELECT unnest(ARRAY['rm_human', 'rm_scheduler', 'rm_agent', 'rm_verifier'])
    LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = group_name) THEN
            BEGIN
                EXECUTE format('CREATE ROLE %I NOLOGIN', group_name);
            EXCEPTION WHEN duplicate_object OR unique_violation THEN
                -- Another migration on this cluster created the shared group.
                NULL;
            END;
        END IF;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = group_name
                AND (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls))
            OR EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member
                WHERE r.rolname = group_name) THEN
            RAISE EXCEPTION 'existing capability group % has unsafe capabilities', group_name;
        END IF;
    END LOOP;
END $$;

CREATE FUNCTION rm.insert_claim(body jsonb, expected_kind rm.claim_kind) RETURNS uuid
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE subject_id uuid;
BEGIN
    IF body -> 'format_version' IS DISTINCT FROM '1'::jsonb
        OR body ->> 'kind' IS DISTINCT FROM expected_kind::text
        OR jsonb_typeof(body -> 'hypotheses') IS DISTINCT FROM 'array'
        OR jsonb_typeof(body -> 'definition_ids') IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'expected a version 1 claim contract of kind %', expected_kind
            USING ERRCODE = '22023';
    END IF;

    INSERT INTO claims (id, kind, origin, domain, title, statement, sensitivity,
                        formal_skeleton, small_case_spec)
    VALUES (coalesce((body ->> 'claim_id')::uuid, gen_random_uuid()), expected_kind,
        (body ->> 'origin')::actor_kind, body ->> 'domain', body ->> 'title',
        body ->> 'statement', body ->> 'sensitivity', body -> 'formal_skeleton', body -> 'small_case_spec')
    RETURNING id INTO subject_id;

    INSERT INTO claim_hypotheses (claim_id, label, statement, formal)
    SELECT subject_id, h ->> 'label', h ->> 'statement', nullif(h -> 'formal', 'null'::jsonb)
    FROM jsonb_array_elements(body -> 'hypotheses') h;

    INSERT INTO claim_conclusion (claim_id, statement, quantifiers, parameters, constant_dependencies)
    SELECT subject_id, c ->> 'statement', c -> 'quantifiers', c -> 'parameters', c -> 'constant_dependencies'
    FROM (SELECT body -> 'conclusion' AS c) part;

    INSERT INTO claim_definitions (claim_id, definition_id)
    SELECT subject_id, value::uuid FROM jsonb_array_elements_text(body -> 'definition_ids');

    RETURN subject_id;
END $$;

CREATE FUNCTION rm.create_claim(body jsonb) RETURNS uuid
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT insert_claim(body, 'researcher_claim')
$$;

CREATE FUNCTION rm.freeze_claim(subject_id uuid, actor_ref text, reason text) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE frozen_hash text;
BEGIN
    UPDATE claims SET status = 'open'
    WHERE id = subject_id AND status = 'draft' AND kind = 'researcher_claim'
    RETURNING contract_sha256 INTO frozen_hash;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'freeze requires a researcher draft' USING ERRCODE = '23514';
    END IF;

    INSERT INTO decisions (claim_id, contract_sha256, action, decided_by, actor_ref, reason)
        VALUES (subject_id, frozen_hash, 'freeze', 'human', actor_ref, reason);

    RETURN frozen_hash;
END $$;

CREATE FUNCTION rm.create_working_lemma(body jsonb, actor_ref text, reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE subject_id uuid;
DECLARE frozen_hash text;
BEGIN
    subject_id := insert_claim(body, 'working_lemma');
    UPDATE claims SET status = 'open' WHERE id = subject_id RETURNING contract_sha256 INTO frozen_hash;
    INSERT INTO decisions (claim_id, contract_sha256, action, decided_by, actor_ref, reason)
        VALUES (subject_id, frozen_hash, 'freeze', 'program', actor_ref, reason);
    RETURN subject_id;
END $$;

CREATE FUNCTION rm.enqueue_job(subject_id uuid, subject_hash text, job_type text,
                               body jsonb, job_budget jsonb, job_priority integer DEFAULT 0)
RETURNS rm.jobs LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE queued jobs;
BEGIN
    IF jsonb_typeof(job_budget -> 'max_attempts') IS DISTINCT FROM 'number'
        OR jsonb_typeof(job_budget -> 'wall_seconds') IS DISTINCT FROM 'number'
        OR (job_budget ->> 'wall_seconds')::integer < 1 THEN
        RAISE EXCEPTION 'job budget needs positive max_attempts and wall_seconds' USING ERRCODE = '22023';
    END IF;
    PERFORM 1 FROM claims c WHERE c.id = subject_id AND c.contract_sha256 = subject_hash
        AND c.status = 'open' FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'job requires the current frozen open claim' USING ERRCODE = '23514';
    END IF;

    IF job_type = 'audit' AND NOT EXISTS (
        SELECT 1 FROM attempts a WHERE a.id = (body ->> 'candidate_attempt_id')::uuid
            AND a.claim_id = subject_id AND a.contract_sha256 = subject_hash AND a.role = 'prove'
            AND a.artifact_sha256 IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'audit job requires a proof attempt for the frozen claim' USING ERRCODE = '23514';
    END IF;

    INSERT INTO jobs (claim_id, contract_sha256, kind, payload, budget, priority, max_attempts)
    VALUES (subject_id, subject_hash, job_type::job_kind, body, job_budget, job_priority,
            (job_budget ->> 'max_attempts')::integer)
    RETURNING * INTO queued;
    RETURN queued;
END $$;

CREATE FUNCTION rm.claim_job(job_type text, worker text, lease_seconds integer,
                             job_id uuid DEFAULT NULL)
RETURNS SETOF rm.jobs LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF nullif(btrim(worker), '') IS NULL OR lease_seconds IS NULL OR lease_seconds NOT BETWEEN 1 AND 86400 THEN
        RAISE EXCEPTION 'claiming worker and bounded lease duration are required' USING ERRCODE = '22023';
    END IF;

    RETURN QUERY
    WITH ready AS (
        SELECT j.id FROM jobs j JOIN claims c ON c.id = j.claim_id
        WHERE j.kind = job_type::job_kind AND (job_id IS NULL OR j.id = job_id)
          AND c.status = 'open'
          AND (j.status = 'queued' OR (j.status = 'running' AND j.lease_until <= clock_timestamp()))
          AND j.lease_generation < j.max_attempts
        ORDER BY j.priority DESC, j.enqueued_at, j.id
        LIMIT 1 FOR UPDATE OF j SKIP LOCKED
    )
    UPDATE jobs j SET status = 'running', lease_owner = worker,
        lease_generation = j.lease_generation + 1,
        lease_until = clock_timestamp() + make_interval(secs => least(
            lease_seconds, (j.budget ->> 'wall_seconds')::integer))
    FROM ready WHERE j.id = ready.id RETURNING j.*;
END $$;

CREATE FUNCTION rm.checkpoint_job(job_id uuid, worker text, generation integer, body jsonb)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    UPDATE jobs j SET checkpoint = body WHERE j.id = job_id AND j.status = 'running'
        AND j.lease_owner = worker AND j.lease_generation = generation AND j.lease_until > clock_timestamp();
    RETURN FOUND;
END $$;

CREATE FUNCTION rm.requeue_job(job_id uuid)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    UPDATE jobs j SET status = 'queued' WHERE j.id = job_id AND j.status = 'failed'
        AND j.lease_generation < j.max_attempts;
    RETURN FOUND;
END $$;

CREATE FUNCTION rm.expire_exhausted_jobs() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE count_updated integer;
BEGIN
    UPDATE jobs SET status = 'failed', lease_owner = NULL, lease_until = NULL
    WHERE status = 'running' AND lease_until <= clock_timestamp() AND lease_generation >= max_attempts;
    GET DIAGNOSTICS count_updated = ROW_COUNT;
    RETURN count_updated;
END $$;

CREATE FUNCTION rm.submit_attempt(job_id uuid, worker text, generation integer, body jsonb)
RETURNS rm.attempts LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE claimed jobs;
DECLARE saved attempts;
DECLARE receipt_hash text;
DECLARE body_model jsonb;
BEGIN
    SELECT * INTO claimed FROM jobs j WHERE j.id = submit_attempt.job_id FOR UPDATE;
    IF NOT FOUND OR (body ->> 'job_id')::uuid IS DISTINCT FROM claimed.id
        OR (body #>> '{subject,claim_id}')::uuid IS DISTINCT FROM claimed.claim_id
        OR (body #>> '{subject,contract_sha256}') IS DISTINCT FROM claimed.contract_sha256
        OR (body ->> 'generation')::integer IS DISTINCT FROM generation
        OR body ->> 'role' IS DISTINCT FROM claimed.kind::text THEN
        RAISE EXCEPTION 'submission does not match its job and claim version' USING ERRCODE = '23514';
    END IF;
    receipt_hash := encode(sha256(convert_to(body::text, 'UTF8')), 'hex');

    -- A retry of the identical committed response can recover its receipt.
    -- A generation replaced by a fresh worker loses all write authority.
    IF claimed.lease_generation = generation AND claimed.status IN ('succeeded', 'failed') THEN
        SELECT * INTO saved FROM attempts a WHERE a.job_id = submit_attempt.job_id
            AND a.lease_generation = generation AND a.lease_owner = worker
            AND a.submission_sha256 = receipt_hash AND a.submitted_by = session_user;
        IF FOUND THEN RETURN saved; END IF;
    END IF;

    IF claimed.status <> 'running' OR claimed.lease_generation IS DISTINCT FROM generation
        OR claimed.lease_owner IS DISTINCT FROM worker OR claimed.lease_until <= clock_timestamp() THEN
        RAISE EXCEPTION 'missing, expired, or superseded job lease' USING ERRCODE = '23514';
    END IF;

    PERFORM 1 FROM claims WHERE id = claimed.claim_id AND status = 'open' FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'submission requires an open claim' USING ERRCODE = '23514';
    END IF;

    body_model := body -> 'model';
    INSERT INTO attempts (job_id, claim_id, contract_sha256, lease_generation, submitted_by, lease_owner,
        submission_sha256, role, route_key, context_sha256, harness_version, model_provider, model_id,
        model_family, model_revision, model_parameters, prompt_sha256, finding, artifact_uri, artifact_sha256,
        transcript_uri, transcript_sha256, failure_reason, input_tokens, output_tokens, cost_usd)
    VALUES (claimed.id, claimed.claim_id, claimed.contract_sha256, generation, session_user, worker,
        receipt_hash, claimed.kind, body ->> 'route_key', body ->> 'context_sha256', body ->> 'harness_version',
        body_model ->> 'provider', body_model ->> 'id', body_model ->> 'family', body_model ->> 'revision',
        body_model -> 'parameters', body_model ->> 'prompt_sha256', (body ->> 'finding')::finding_type,
        body #>> '{artifact,uri}', body #>> '{artifact,sha256}', body #>> '{transcript,uri}',
        body #>> '{transcript,sha256}', body ->> 'failure_reason', (body_model ->> 'input_tokens')::bigint,
        (body_model ->> 'output_tokens')::bigint, (body_model ->> 'cost_usd')::numeric)
    RETURNING * INTO saved;

    UPDATE jobs SET status = CASE WHEN saved.finding = 'no_result' THEN 'failed'::job_status
                                  ELSE 'succeeded'::job_status END,
        lease_owner = NULL, lease_until = NULL WHERE id = claimed.id;
    RETURN saved;
END $$;

CREATE FUNCTION rm.issue_evidence(body jsonb) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE receipt_id uuid;
BEGIN
    INSERT INTO evidence (claim_id, contract_sha256, attempt_id, audit_attempt_id, kind, verdict, diff_status,
        audit_mode, artifact_uri, artifact_sha256, verifier_name, verifier_version, code_revision,
        environment_sha256, result)
    VALUES ((body #>> '{subject,claim_id}')::uuid, body #>> '{subject,contract_sha256}',
        (body ->> 'attempt_id')::uuid, (body ->> 'audit_attempt_id')::uuid, (body ->> 'kind')::evidence_kind,
        (body ->> 'verdict')::verdict, (body ->> 'diff_status')::scope_status, body ->> 'audit_mode',
        body #>> '{artifact,uri}', body #>> '{artifact,sha256}', body #>> '{verifier,name}',
        body #>> '{verifier,version}', body #>> '{provenance,code_revision}',
        body #>> '{provenance,environment_sha256}', body -> 'result')
    RETURNING id INTO receipt_id;
    RETURN receipt_id;
END $$;

CREATE FUNCTION rm.review_evidence(evidence_id uuid, action text, actor_ref text, reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE receipt evidence;
DECLARE decision_id uuid;
BEGIN
    IF action IS NULL OR action NOT IN ('approve', 'return') THEN
        RAISE EXCEPTION 'review requires approve or return' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO STRICT receipt FROM evidence WHERE id = review_evidence.evidence_id;
    PERFORM 1 FROM claims WHERE id = receipt.claim_id AND status = 'open' FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'review requires an open claim' USING ERRCODE = '23514';
    END IF;
    IF action = 'approve' AND (receipt.verdict <> 'verified' OR receipt.diff_status <> 'exact') THEN
        RAISE EXCEPTION 'approval requires a verified receipt for the exact claim' USING ERRCODE = '23514';
    END IF;
    INSERT INTO decisions (claim_id, contract_sha256, evidence_id, action, decided_by, actor_ref, reason)
        VALUES (receipt.claim_id, receipt.contract_sha256, receipt.id, action, 'human', actor_ref, reason)
    RETURNING id INTO decision_id;
    RETURN decision_id;
END $$;

CREATE FUNCTION rm.promote_claim(evidence_id uuid, reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE receipt evidence;
BEGIN
    SELECT * INTO STRICT receipt FROM evidence WHERE id = promote_claim.evidence_id;
    UPDATE claims SET status = CASE WHEN receipt.kind = 'counterexample' THEN 'refuted'::claim_status
                                    ELSE 'proved'::claim_status END,
        decisive_evidence_id = receipt.id WHERE id = receipt.claim_id AND status = 'open';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'promotion requires an open claim' USING ERRCODE = '23514';
    END IF;
    INSERT INTO decisions (claim_id, contract_sha256, evidence_id, action, decided_by, actor_ref, reason)
        VALUES (receipt.claim_id, receipt.contract_sha256, receipt.id, 'promote', 'program', session_user, reason);
    RETURN receipt.claim_id;
END $$;

CREATE VIEW rm.shared_evidence WITH (security_barrier = true) AS
    SELECT e.* FROM rm.evidence e JOIN rm.claims c ON c.id = e.claim_id
    WHERE e.verdict = 'verified' AND e.diff_status = 'exact' AND c.sensitivity = 'public';

REVOKE EXECUTE ON ALL ROUTINES IN SCHEMA rm FROM PUBLIC;
GRANT USAGE ON SCHEMA rm TO rm_human, rm_scheduler, rm_agent, rm_verifier;
GRANT SELECT ON rm.claims, rm.definitions, rm.claim_definitions, rm.claim_hypotheses,
    rm.claim_conclusion, rm.claim_edges TO rm_human, rm_scheduler, rm_verifier;
GRANT SELECT ON rm.jobs, rm.attempts, rm.evidence, rm.decisions TO rm_human, rm_verifier;
GRANT SELECT ON rm.jobs, rm.shared_evidence TO rm_scheduler;
GRANT EXECUTE ON FUNCTION rm.create_claim(jsonb),
    rm.freeze_claim(uuid, text, text),
    rm.review_evidence(uuid, text, text, text) TO rm_human;
GRANT EXECUTE ON FUNCTION rm.create_working_lemma(jsonb, text, text),
    rm.enqueue_job(uuid, text, text, jsonb, jsonb, integer),
    rm.claim_job(text, text, integer, uuid), rm.checkpoint_job(uuid, text, integer, jsonb),
    rm.requeue_job(uuid), rm.expire_exhausted_jobs() TO rm_scheduler;
GRANT EXECUTE ON FUNCTION rm.submit_attempt(uuid, text, integer, jsonb) TO rm_agent;
GRANT EXECUTE ON FUNCTION rm.issue_evidence(jsonb), rm.promote_claim(uuid, text) TO rm_verifier;
