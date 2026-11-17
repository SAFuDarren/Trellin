CREATE TABLE rm.lean_search_budgets (
    claim_id uuid NOT NULL,
    contract_sha256 rm.sha256_hex NOT NULL,
    call_limit integer NOT NULL DEFAULT 8 CHECK (call_limit >= 0),
    reserved_calls bigint NOT NULL DEFAULT 0 CHECK (reserved_calls >= 0),
    PRIMARY KEY (claim_id, contract_sha256),
    FOREIGN KEY (claim_id, contract_sha256) REFERENCES rm.claims(id, contract_sha256)
);

CREATE TABLE rm.lean_search_budget_changes (
    sequence_no bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    claim_id uuid NOT NULL,
    contract_sha256 rm.sha256_hex NOT NULL,
    previous_limit integer NOT NULL,
    call_limit integer NOT NULL,
    actor_ref text NOT NULL CHECK (btrim(actor_ref) <> ''),
    authenticated_role name NOT NULL DEFAULT session_user,
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    changed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (claim_id, contract_sha256) REFERENCES rm.lean_search_budgets(claim_id, contract_sha256)
);
CREATE INDEX lean_search_budget_changes_by_scope ON rm.lean_search_budget_changes
    (claim_id, contract_sha256, sequence_no);

CREATE TABLE rm.lean_search_call_slots (
    job_id uuid NOT NULL,
    lease_generation integer NOT NULL CHECK (lease_generation > 0),
    claim_id uuid NOT NULL,
    contract_sha256 rm.sha256_hex NOT NULL,
    reserved_at timestamptz,
    PRIMARY KEY (job_id, lease_generation),
    FOREIGN KEY (job_id, claim_id, contract_sha256) REFERENCES rm.jobs(id, claim_id, contract_sha256)
);
CREATE INDEX lean_search_call_slots_by_scope ON rm.lean_search_call_slots (claim_id, contract_sha256);

CREATE TRIGGER lean_search_budget_changes_append_only BEFORE UPDATE OR DELETE ON rm.lean_search_budget_changes
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();
CREATE TRIGGER lean_search_call_slots_append_only BEFORE UPDATE OR DELETE ON rm.lean_search_call_slots
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

-- Earlier leases may already have contacted a model, even without a submitted attempt.
-- Leave their dispatch times unknown and account for them before offering any new slots.
LOCK TABLE rm.jobs IN SHARE ROW EXCLUSIVE MODE;
INSERT INTO rm.lean_search_call_slots (job_id, lease_generation, claim_id, contract_sha256)
    SELECT j.id, lease.number, j.claim_id, j.contract_sha256 FROM rm.jobs j
    CROSS JOIN LATERAL generate_series(1, j.lease_generation) AS lease(number)
    WHERE j.kind = 'prove' AND j.payload -> 'lean_search' = 'true'::jsonb;

INSERT INTO rm.lean_search_budgets (claim_id, contract_sha256, reserved_calls)
    SELECT claim_id, contract_sha256, count(*) FROM rm.lean_search_call_slots
    GROUP BY claim_id, contract_sha256;

CREATE FUNCTION rm.reserve_lean_search_call() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    INSERT INTO lean_search_budgets (claim_id, contract_sha256)
        VALUES (NEW.claim_id, NEW.contract_sha256) ON CONFLICT DO NOTHING;
    UPDATE lean_search_budgets SET reserved_calls = reserved_calls + 1
        WHERE claim_id = NEW.claim_id AND contract_sha256 = NEW.contract_sha256
          AND reserved_calls < call_limit;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Frozen goal Lean search call limit reached.' USING ERRCODE = 'RMB01';
    END IF;
    INSERT INTO lean_search_call_slots (job_id, lease_generation, claim_id, contract_sha256, reserved_at)
        VALUES (NEW.id, NEW.lease_generation, NEW.claim_id, NEW.contract_sha256, clock_timestamp());
    RETURN NEW;
END $$;
CREATE TRIGGER reserve_lean_search_call BEFORE UPDATE OF lease_generation ON rm.jobs
    FOR EACH ROW WHEN (NEW.kind = 'prove' AND NEW.payload -> 'lean_search' = 'true'::jsonb
                      AND NEW.lease_generation > OLD.lease_generation)
    EXECUTE FUNCTION rm.reserve_lean_search_call();

CREATE FUNCTION rm.claim_budgeted_lean_search(worker text, lease_seconds integer, job_id uuid, retry boolean)
RETURNS TABLE (lease_generation integer, budget_exhausted boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM jobs j WHERE j.id = job_id AND j.kind = 'prove'
                   AND j.payload -> 'lean_search' = 'true'::jsonb) THEN
        RAISE EXCEPTION 'A budgeted claim needs a public Lean search job.' USING ERRCODE = '22023';
    END IF;
    IF retry THEN
        PERFORM requeue_job(job_id);
    END IF;
    RETURN QUERY SELECT j.lease_generation, false FROM claim_job('prove', worker, lease_seconds, job_id) j;
EXCEPTION WHEN SQLSTATE 'RMB01' THEN
    -- Roll back the requeue as well as the denied lease so a retry can still recover the old failure.
    RETURN QUERY SELECT NULL::integer, true;
END $$;

CREATE VIEW rm.lean_search_budget_status WITH (security_barrier = true) AS
    SELECT s.id AS statement_id, s.claim_id, s.contract_sha256, coalesce(b.call_limit, 8) AS call_limit,
           coalesce(b.reserved_calls, 0) AS reserved_calls,
           greatest(coalesce(b.call_limit - b.reserved_calls, 8), 0) AS remaining_calls
    FROM rm.lean_statements s
    JOIN rm.lean_statement_approvals a ON a.statement_id = s.id
    JOIN rm.claims c ON (c.id, c.contract_sha256) = (s.claim_id, s.contract_sha256)
    LEFT JOIN rm.lean_search_budgets b ON (b.claim_id, b.contract_sha256) = (s.claim_id, s.contract_sha256)
    WHERE c.sensitivity = 'public' AND s.body ->> 'theorem' = 'RMTheory.expected';

CREATE FUNCTION rm.set_lean_search_call_limit(target_statement_id uuid, new_limit integer, actor_ref text, reason text)
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE scope record;
DECLARE previous integer;
BEGIN
    IF new_limit IS NULL OR new_limit < 0 OR nullif(btrim(actor_ref), '') IS NULL
        OR nullif(btrim(reason), '') IS NULL THEN
        RAISE EXCEPTION 'A nonnegative call limit, actor and reason are required.' USING ERRCODE = '22023';
    END IF;
    SELECT b.claim_id, b.contract_sha256 INTO scope FROM lean_search_budget_status b
        WHERE b.statement_id = target_statement_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'A confirmed public Lean statement is required.' USING ERRCODE = '23514';
    END IF;
    INSERT INTO lean_search_budgets (claim_id, contract_sha256)
        VALUES (scope.claim_id, scope.contract_sha256) ON CONFLICT DO NOTHING;
    SELECT b.call_limit INTO STRICT previous FROM lean_search_budgets b
        WHERE (b.claim_id, b.contract_sha256) = (scope.claim_id, scope.contract_sha256) FOR UPDATE;
    IF previous <> new_limit THEN
        UPDATE lean_search_budgets b SET call_limit = new_limit
            WHERE (b.claim_id, b.contract_sha256) = (scope.claim_id, scope.contract_sha256);
        INSERT INTO lean_search_budget_changes (claim_id, contract_sha256, previous_limit, call_limit, actor_ref, reason)
            VALUES (scope.claim_id, scope.contract_sha256, previous, new_limit, actor_ref, reason);
    END IF;
    RETURN new_limit;
END $$;

CREATE VIEW rm.lean_search_call_history WITH (security_barrier = true) AS
    SELECT s.claim_id, s.contract_sha256, s.job_id, s.lease_generation, s.reserved_at, j.operation_key,
           j.payload ->> 'search_route' AS search_route, j.payload -> 'round' AS round, a.id AS attempt_id,
           CASE
               WHEN a.finding = 'no_result' THEN 'failed'
               WHEN a.id IS NOT NULL THEN 'responded'
               WHEN j.lease_generation = s.lease_generation AND j.status = 'running'
                   AND j.lease_until > clock_timestamp() THEN 'leased'
               ELSE 'no_report'
           END AS status,
           a.model_provider, a.model_id, a.input_tokens, a.output_tokens, a.cost_usd::text AS cost_usd,
           a.submitted_at,
           m.execution_ms, m.queue_ms, m.failure_code, a.failure_reason, m.retryable
    FROM rm.lean_search_call_slots s
    JOIN rm.jobs j ON j.id = s.job_id
    LEFT JOIN rm.attempts a ON a.job_id = s.job_id AND a.lease_generation = s.lease_generation
    LEFT JOIN rm.attempt_measurements m ON m.attempt_id = a.id;

REVOKE EXECUTE ON FUNCTION rm.reserve_lean_search_call(), rm.claim_budgeted_lean_search(text, integer, uuid, boolean),
    rm.set_lean_search_call_limit(uuid, integer, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.claim_budgeted_lean_search(text, integer, uuid, boolean) TO rm_scheduler;
GRANT EXECUTE ON FUNCTION rm.set_lean_search_call_limit(uuid, integer, text, text) TO rm_human;
GRANT SELECT ON rm.lean_search_budget_status, rm.lean_search_call_history, rm.lean_search_budget_changes TO rm_human;
