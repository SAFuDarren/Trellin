-- Each database belongs to one bound workspace. Use UTC months and integer
-- microdollars so concurrent reservations never round or overspend.
CREATE TABLE rm.project_model_limits (
    sequence_no bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    monthly_microusd bigint CHECK (monthly_microusd BETWEEN 0 AND 100000000000000),
    actor_ref text NOT NULL CHECK (btrim(actor_ref) <> ''),
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    authenticated_role name NOT NULL DEFAULT session_user,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER project_model_limits_immutable BEFORE UPDATE OR DELETE ON rm.project_model_limits
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE TABLE rm.project_model_tariffs (
    sequence_no bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    route_sha256 rm.sha256_hex NOT NULL,
    route jsonb NOT NULL CHECK (jsonb_typeof(route) = 'object'),
    maximum_microusd bigint NOT NULL CHECK (maximum_microusd BETWEEN 0 AND 100000000000000),
    max_input_bytes integer NOT NULL CHECK (max_input_bytes BETWEEN 1 AND 16000000),
    max_output_tokens integer NOT NULL CHECK (max_output_tokens BETWEEN 1 AND 1000000),
    actor_ref text NOT NULL CHECK (btrim(actor_ref) <> ''),
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    authenticated_role name NOT NULL DEFAULT session_user,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX project_model_tariff_latest ON rm.project_model_tariffs(route_sha256, sequence_no DESC);
CREATE TRIGGER project_model_tariffs_immutable BEFORE UPDATE OR DELETE ON rm.project_model_tariffs
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE TABLE rm.project_model_months (
    period date PRIMARY KEY,
    reserved_microusd bigint NOT NULL DEFAULT 0 CHECK (reserved_microusd >= 0)
);
CREATE TABLE rm.project_model_dispatches (
    id uuid PRIMARY KEY,
    period date NOT NULL REFERENCES rm.project_model_months(period),
    tariff_id bigint NOT NULL REFERENCES rm.project_model_tariffs(sequence_no),
    service text NOT NULL CHECK (length(service) BETWEEN 1 AND 100),
    input_bytes integer NOT NULL CHECK (input_bytes > 0),
    output_token_cap integer NOT NULL CHECK (output_token_cap > 0),
    reserved_microusd bigint NOT NULL CHECK (reserved_microusd >= 0),
    authenticated_role name NOT NULL DEFAULT session_user,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX project_model_dispatch_by_period ON rm.project_model_dispatches(period);
CREATE TRIGGER project_model_dispatches_immutable BEFORE UPDATE OR DELETE ON rm.project_model_dispatches
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE FUNCTION rm.set_project_model_limit(cap bigint, actor_ref text, reason text)
RETURNS rm.project_model_limits LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE saved project_model_limits;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('rm.project_model_budget', 49));
    INSERT INTO project_model_limits(monthly_microusd, actor_ref, reason)
        VALUES ($1, $2, $3) RETURNING * INTO saved;
    RETURN saved;
END $$;

CREATE FUNCTION rm.set_project_model_tariff(route_sha256 text, route jsonb, cap bigint,
        max_input_bytes integer, max_output_tokens integer, actor_ref text, reason text)
RETURNS rm.project_model_tariffs LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE saved project_model_tariffs;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('rm.project_model_budget', 49));
    INSERT INTO project_model_tariffs(route_sha256, route, maximum_microusd, max_input_bytes, max_output_tokens,
                                     actor_ref, reason)
        VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING * INTO saved;
    RETURN saved;
END $$;

CREATE FUNCTION rm.reserve_project_model(route_sha256 text, service text, input_bytes integer,
        output_token_cap integer, output_limit_enforced boolean)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE month_start date := date_trunc('month', statement_timestamp() AT TIME ZONE 'UTC')::date;
DECLARE cap bigint;
DECLARE tariff project_model_tariffs;
DECLARE dispatch_id uuid := gen_random_uuid();
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('rm.project_model_budget', 49));
    SELECT monthly_microusd INTO cap FROM project_model_limits ORDER BY sequence_no DESC LIMIT 1;
    IF cap IS NULL THEN RETURN NULL; END IF;
    SELECT * INTO tariff FROM project_model_tariffs t WHERE t.route_sha256 = $1
        ORDER BY sequence_no DESC LIMIT 1;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'The model route needs a researcher-approved maximum charge.' USING ERRCODE = 'RMB03';
    END IF;
    IF NOT coalesce($3 BETWEEN 1 AND tariff.max_input_bytes AND $4 BETWEEN 1 AND tariff.max_output_tokens, false)
       OR (tariff.maximum_microusd > 0 AND $5 IS DISTINCT FROM true) THEN
        RAISE EXCEPTION 'The request exceeds the approved model rate envelope.' USING ERRCODE = 'RMB03';
    END IF;
    INSERT INTO project_model_months(period) VALUES (month_start) ON CONFLICT DO NOTHING;
    UPDATE project_model_months m SET reserved_microusd = m.reserved_microusd + tariff.maximum_microusd
        WHERE m.period = month_start AND m.reserved_microusd + tariff.maximum_microusd <= cap;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'The monthly project model allowance is exhausted.' USING ERRCODE = 'RMB02';
    END IF;
    INSERT INTO project_model_dispatches (id, period, tariff_id, service, input_bytes, output_token_cap, reserved_microusd)
        VALUES (dispatch_id, month_start, tariff.sequence_no, $2, $3, $4, tariff.maximum_microusd);
    RETURN dispatch_id;
END $$;

REVOKE ALL ON FUNCTION rm.set_project_model_limit(bigint,text,text),
    rm.set_project_model_tariff(text,jsonb,bigint,integer,integer,text,text),
    rm.reserve_project_model(text,text,integer,integer,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.set_project_model_limit(bigint,text,text),
    rm.set_project_model_tariff(text,jsonb,bigint,integer,integer,text,text) TO rm_human;
GRANT EXECUTE ON FUNCTION rm.reserve_project_model(text,text,integer,integer,boolean)
    TO rm_human, rm_scheduler, rm_agent, rm_verifier;
GRANT SELECT ON rm.project_model_limits, rm.project_model_tariffs, rm.project_model_months,
    rm.project_model_dispatches TO rm_human;

CREATE FUNCTION rm.project_model_epoch() RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT concat_ws(':', to_char(statement_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM'),
           policy.sequence_no, (SELECT max(sequence_no) FROM project_model_tariffs))
    FROM (SELECT sequence_no, monthly_microusd FROM project_model_limits ORDER BY sequence_no DESC LIMIT 1) policy
    WHERE policy.monthly_microusd IS NOT NULL
$$;
REVOKE ALL ON FUNCTION rm.project_model_epoch() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.project_model_epoch() TO rm_human, rm_scheduler, rm_agent, rm_verifier;
