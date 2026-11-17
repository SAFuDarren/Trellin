CREATE TABLE rm.formalization_reviews (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    active_version integer NOT NULL DEFAULT 1 CHECK (active_version > 0),
    gate_revision bigint NOT NULL DEFAULT 0
);

CREATE TABLE rm.formalization_packets (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    review_id uuid NOT NULL REFERENCES rm.formalization_reviews(id),
    claim_id uuid REFERENCES rm.claims(id),
    version integer NOT NULL CHECK (version > 0),
    packet jsonb NOT NULL CHECK (jsonb_typeof(packet) = 'object'),
    claim_contract jsonb,
    packet_sha256 rm.sha256_hex NOT NULL,
    proposed_by name NOT NULL DEFAULT session_user,
    proposed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK ((claim_id IS NULL) = (claim_contract IS NULL)),
    UNIQUE (review_id, version)
);
CREATE INDEX formalization_packets_claim ON rm.formalization_packets(claim_id, version DESC);

CREATE TABLE rm.formalization_approvals (
    packet_id uuid PRIMARY KEY REFERENCES rm.formalization_packets(id),
    confirmed_item_ids text[] NOT NULL,
    approved_by text NOT NULL CHECK (btrim(approved_by) <> ''),
    authenticated_role name NOT NULL DEFAULT session_user,
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    approved_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE rm.clarification_requests (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    packet_id uuid NOT NULL REFERENCES rm.formalization_packets(id),
    item_id text NOT NULL,
    question text NOT NULL,
    impact text NOT NULL,
    requested_by name NOT NULL DEFAULT session_user,
    requested_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX clarification_requests_packet ON rm.clarification_requests(packet_id);

CREATE TABLE rm.clarification_resolutions (
    request_id uuid PRIMARY KEY REFERENCES rm.clarification_requests(id),
    resolved_by text NOT NULL CHECK (btrim(resolved_by) <> ''),
    authenticated_role name NOT NULL DEFAULT session_user,
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    resolved_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TRIGGER formalization_packets_append_only BEFORE UPDATE OR DELETE ON rm.formalization_packets
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();
CREATE TRIGGER formalization_approvals_append_only BEFORE UPDATE OR DELETE ON rm.formalization_approvals
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();
CREATE TRIGGER clarification_requests_append_only BEFORE UPDATE OR DELETE ON rm.clarification_requests
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();
CREATE TRIGGER clarification_resolutions_append_only BEFORE UPDATE OR DELETE ON rm.clarification_resolutions
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE FUNCTION rm.propose_formalization(packet jsonb, canonical_claim jsonb DEFAULT NULL,
                                         previous_packet_id uuid DEFAULT NULL)
RETURNS rm.formalization_packets
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE previous formalization_packets;
DECLARE saved formalization_packets;
DECLARE series_id uuid;
DECLARE series_version integer;
DECLARE subject_id uuid;
BEGIN
    IF packet -> 'format_version' IS DISTINCT FROM '1'::jsonb
        OR jsonb_typeof(packet -> 'items') IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'expected a version 1 formalization packet with an items array' USING ERRCODE = '22023';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM jsonb_array_elements(packet -> 'items') i WHERE i ->> 'kind' = 'goal')
        OR EXISTS (SELECT 1 FROM jsonb_array_elements(packet -> 'items') i WHERE
            jsonb_typeof(i -> 'id') IS DISTINCT FROM 'string' OR nullif(btrim(i ->> 'id'), '') IS NULL
            OR jsonb_typeof(i -> 'kind') IS DISTINCT FROM 'string'
            OR i ->> 'kind' NOT IN ('goal', 'definition', 'hypothesis', 'theorem')
            OR jsonb_typeof(i -> 'natural') IS DISTINCT FROM 'string' OR nullif(btrim(i ->> 'natural'), '') IS NULL
            OR jsonb_typeof(i -> 'formal') IS DISTINCT FROM 'object'
            OR jsonb_typeof(i -> 'status') IS DISTINCT FROM 'string'
            OR i ->> 'status' NOT IN ('stated', 'needs_confirmation')
            OR (i ? 'question' AND (i ->> 'status' <> 'needs_confirmation'
                OR jsonb_typeof(i -> 'question') IS DISTINCT FROM 'string'
                OR nullif(btrim(i ->> 'question'), '') IS NULL)))
        OR EXISTS (SELECT 1 FROM jsonb_array_elements(packet -> 'items') i GROUP BY i ->> 'id' HAVING count(*) > 1)
    THEN
        RAISE EXCEPTION 'packet requires a goal, unique item IDs, and valid stated or unresolved items'
            USING ERRCODE = '22023';
    END IF;

    IF previous_packet_id IS NULL THEN
        INSERT INTO formalization_reviews DEFAULT VALUES RETURNING id, active_version INTO series_id, series_version;
    ELSE
        SELECT * INTO STRICT previous FROM formalization_packets p WHERE p.id = previous_packet_id;
        UPDATE formalization_reviews r SET active_version = r.active_version + 1
        WHERE r.id = previous.review_id AND r.active_version = previous.version
        RETURNING r.id, r.active_version INTO series_id, series_version;
        IF NOT FOUND OR EXISTS (SELECT 1 FROM formalization_approvals a WHERE a.packet_id = previous.id) THEN
            RAISE EXCEPTION 'revision requires the current unapproved packet' USING ERRCODE = '23514';
        END IF;
    END IF;

    IF canonical_claim IS NULL THEN
        canonical_claim := previous.claim_contract;
        subject_id := previous.claim_id;
    ELSE
        subject_id := insert_claim(canonical_claim, 'researcher_claim');
    END IF;
    IF packet ->> 'claim_id' IS DISTINCT FROM subject_id::text THEN
        RAISE EXCEPTION 'packet claim_id does not match the proposed claim' USING ERRCODE = '23514';
    END IF;

    INSERT INTO formalization_packets (review_id, claim_id, version, packet, claim_contract, packet_sha256)
    VALUES (series_id, subject_id, series_version, packet, canonical_claim,
        encode(sha256(convert_to(jsonb_build_object('packet', packet, 'claim_contract', canonical_claim)::text, 'UTF8')), 'hex'))
    RETURNING * INTO saved;
    RETURN saved;
END $$;

CREATE FUNCTION rm.confirm_formalization(packet_id uuid, expected_version integer, expected_sha256 text,
                                         confirmed_item_ids text[], actor_ref text, reason text)
RETURNS TABLE (claim_id uuid, contract_sha256 text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE target formalization_packets;
DECLARE approval formalization_approvals;
DECLARE unresolved text[];
DECLARE declared text[];
BEGIN
    SELECT * INTO STRICT target FROM formalization_packets p WHERE p.id = confirm_formalization.packet_id;
    IF target.version IS DISTINCT FROM expected_version OR target.packet_sha256 IS DISTINCT FROM expected_sha256 THEN
        RAISE EXCEPTION 'confirmation must match the packet version and hash' USING ERRCODE = '23514';
    END IF;
    IF nullif(btrim(actor_ref), '') IS NULL OR nullif(btrim(reason), '') IS NULL THEN
        RAISE EXCEPTION 'confirmation needs a researcher name and reason' USING ERRCODE = '22023';
    END IF;
    SELECT ARRAY(SELECT i ->> 'id' FROM jsonb_array_elements(target.packet -> 'items') i
                  WHERE i ->> 'status' = 'needs_confirmation' ORDER BY i ->> 'id' COLLATE "C") INTO unresolved;
    SELECT ARRAY(SELECT val FROM unnest(confirmed_item_ids) val ORDER BY val COLLATE "C") INTO declared;
    IF confirmed_item_ids IS NULL OR declared IS DISTINCT FROM unresolved THEN
        RAISE EXCEPTION 'confirmation requires exactly the unresolved item IDs' USING ERRCODE = '23514';
    END IF;

    PERFORM 1 FROM formalization_reviews r
    WHERE r.id = target.review_id AND r.active_version = target.version FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'only the current packet can be confirmed' USING ERRCODE = '23514';
    END IF;
    SELECT * INTO approval FROM formalization_approvals a WHERE a.packet_id = target.id;
    claim_id := target.claim_id;
    IF FOUND THEN
        IF approval.approved_by <> actor_ref OR approval.authenticated_role <> session_user
            OR approval.reason <> reason THEN
            RAISE EXCEPTION 'packet already has a different confirmation' USING ERRCODE = '23514';
        END IF;
        SELECT c.contract_sha256 INTO contract_sha256 FROM claims c WHERE c.id = target.claim_id;
    ELSE
        UPDATE formalization_reviews r SET gate_revision = r.gate_revision + 1 WHERE r.id = target.review_id;
        INSERT INTO formalization_approvals (packet_id, confirmed_item_ids, approved_by, reason)
        VALUES (target.id, declared, actor_ref, reason);
        IF target.claim_id IS NOT NULL THEN
            contract_sha256 := freeze_claim(target.claim_id, actor_ref, reason);
        END IF;
    END IF;
    RETURN NEXT;
END $$;

CREATE FUNCTION rm.guard_aligned_freeze() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM formalization_packets p WHERE p.claim_id = NEW.id)
        AND NOT EXISTS (
            SELECT 1 FROM formalization_packets p
            JOIN formalization_reviews r ON r.id = p.review_id AND r.active_version = p.version
            JOIN formalization_approvals a ON a.packet_id = p.id
            WHERE p.claim_id = NEW.id AND p.claim_contract = NEW.contract_snapshot
        ) THEN
        RAISE EXCEPTION 'freezing an aligned claim requires confirmation of the same canonical claim'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END $$;
CREATE TRIGGER check_aligned_freeze AFTER UPDATE OF status ON rm.claims
    FOR EACH ROW WHEN (OLD.status = 'draft' AND NEW.status = 'open') EXECUTE FUNCTION rm.guard_aligned_freeze();

CREATE FUNCTION rm.request_clarification(packet_id uuid, item_id text, question text, impact text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE target formalization_packets;
DECLARE prior clarification_requests;
BEGIN
    SELECT * INTO STRICT target FROM formalization_packets p WHERE p.id = request_clarification.packet_id;
    IF nullif(btrim(question), '') IS NULL OR length(btrim(question)) > 1000
        OR nullif(btrim(impact), '') IS NULL OR length(btrim(impact)) > 2000
        OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements(target.packet -> 'items') i WHERE i ->> 'id' = item_id)
    THEN
        RAISE EXCEPTION 'clarification requires an existing item, a specific question, and its impact'
            USING ERRCODE = '22023';
    END IF;
    PERFORM 1 FROM formalization_reviews r
    WHERE r.id = target.review_id AND r.active_version = target.version FOR UPDATE;
    IF NOT FOUND OR NOT EXISTS (SELECT 1 FROM formalization_approvals a WHERE a.packet_id = target.id) THEN
        RAISE EXCEPTION 'clarification requires a current approved packet' USING ERRCODE = '23514';
    END IF;
    SELECT * INTO prior FROM clarification_requests c WHERE c.packet_id = target.id
        AND c.item_id = request_clarification.item_id AND c.question = btrim(request_clarification.question);
    IF NOT FOUND THEN
        UPDATE formalization_reviews r SET gate_revision = r.gate_revision + 1 WHERE r.id = target.review_id;
        INSERT INTO clarification_requests (packet_id, item_id, question, impact)
        VALUES (target.id, item_id, btrim(question), btrim(impact)) RETURNING * INTO prior;
    END IF;
    RETURN prior.id;
END $$;

CREATE FUNCTION rm.resolve_clarification(request_id uuid, actor_ref text, reason text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE target clarification_requests;
DECLARE prior clarification_resolutions;
DECLARE series_id uuid;
BEGIN
    IF nullif(btrim(actor_ref), '') IS NULL OR nullif(btrim(reason), '') IS NULL THEN
        RAISE EXCEPTION 'resolution requires a researcher name and reason' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO STRICT target FROM clarification_requests c WHERE c.id = request_id;
    SELECT r.id INTO series_id FROM formalization_reviews r JOIN formalization_packets p ON r.id = p.review_id
    WHERE p.id = target.packet_id AND r.active_version = p.version FOR UPDATE OF r;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'clarification requires the current packet' USING ERRCODE = '23514';
    END IF;
    SELECT * INTO prior FROM clarification_resolutions v WHERE v.request_id = target.id;
    IF FOUND THEN
        IF prior.resolved_by <> actor_ref OR prior.authenticated_role <> session_user OR prior.reason <> reason THEN
            RAISE EXCEPTION 'clarification already has a different resolution' USING ERRCODE = '23514';
        END IF;
    ELSE
        UPDATE formalization_reviews r SET gate_revision = r.gate_revision + 1 WHERE r.id = series_id;
        INSERT INTO clarification_resolutions (request_id, resolved_by, reason) VALUES (target.id, actor_ref, reason);
    END IF;
    RETURN target.id;
END $$;

CREATE VIEW rm.formalization_clarification_status WITH (security_barrier = true) AS
    SELECT q.*, v.request_id IS NOT NULL AS resolved, v.resolved_by, v.resolved_at, v.reason AS resolution_reason
    FROM rm.clarification_requests q LEFT JOIN rm.clarification_resolutions v ON v.request_id = q.id;

CREATE VIEW rm.formalization_status WITH (security_barrier = true) AS
    SELECT p.id, p.review_id, p.claim_id, p.version, p.packet, p.packet_sha256, p.proposed_by, p.proposed_at,
        a.packet_id IS NOT NULL AS approved, a.approved_by, a.approved_at,
        p.version <> r.active_version AS superseded, pending.count AS pending_clarifications,
        p.claim_id IS NOT NULL AND p.version = r.active_version AND a.packet_id IS NOT NULL
            AND pending.count = 0 AS proof_ready,
        a.confirmed_item_ids, a.reason AS approval_reason
    FROM rm.formalization_packets p
    JOIN rm.formalization_reviews r ON r.id = p.review_id
    LEFT JOIN rm.formalization_approvals a ON a.packet_id = p.id
    CROSS JOIN LATERAL (
        SELECT count(*)::integer FROM rm.formalization_clarification_status c WHERE c.packet_id = p.id AND NOT c.resolved
    ) pending;

-- The shared review row is the concurrency fence for formal jobs and clarification.
CREATE FUNCTION rm.require_formalization_ready(subject_id uuid, proof_payload jsonb DEFAULT NULL)
RETURNS void LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE series_id uuid;
BEGIN
    SELECT p.review_id INTO series_id FROM formalization_packets p WHERE p.claim_id = subject_id LIMIT 1;
    IF series_id IS NULL THEN
        IF proof_payload ?| ARRAY['formalization_id', 'formalization_sha256'] THEN
            RAISE EXCEPTION 'proof names a formalization not assigned to its claim' USING ERRCODE = '23514';
        END IF;
        RETURN;
    END IF;
    PERFORM 1 FROM formalization_reviews r WHERE r.id = series_id FOR SHARE;
    IF NOT EXISTS (
        SELECT 1 FROM formalization_status s WHERE s.claim_id = subject_id AND s.proof_ready
          AND (proof_payload IS NULL OR proof_payload @> jsonb_build_object(
              'formalization_id', s.id, 'formalization_sha256', s.packet_sha256))
    ) THEN
        RAISE EXCEPTION 'proof requires the current approved formalization and resolved clarifications'
            USING ERRCODE = '23514';
    END IF;
END $$;

CREATE FUNCTION rm.guard_formalization_job() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    PERFORM require_formalization_ready(NEW.claim_id, CASE WHEN NEW.kind = 'prove' THEN NEW.payload END);
    RETURN NEW;
END $$;
CREATE TRIGGER check_formalization_job BEFORE INSERT ON rm.jobs
    FOR EACH ROW WHEN (NEW.kind IN ('prove', 'audit', 'verify')) EXECUTE FUNCTION rm.guard_formalization_job();
CREATE TRIGGER check_formalization_lease BEFORE UPDATE OF lease_generation ON rm.jobs
    FOR EACH ROW WHEN (NEW.kind IN ('prove', 'audit', 'verify') AND NEW.lease_generation > OLD.lease_generation)
    EXECUTE FUNCTION rm.guard_formalization_job();

CREATE FUNCTION rm.guard_formalization_output() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF TG_TABLE_NAME = 'claims' THEN
        PERFORM require_formalization_ready(NEW.id);
    ELSE
        PERFORM require_formalization_ready(NEW.claim_id);
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER check_formalization_attempt BEFORE INSERT ON rm.attempts
    FOR EACH ROW WHEN (NEW.role IN ('prove', 'audit', 'verify')) EXECUTE FUNCTION rm.guard_formalization_output();
CREATE TRIGGER check_formalization_evidence BEFORE INSERT ON rm.evidence
    FOR EACH ROW EXECUTE FUNCTION rm.guard_formalization_output();
CREATE TRIGGER check_formalization_promotion BEFORE UPDATE OF status ON rm.claims
    FOR EACH ROW WHEN (OLD.status = 'open' AND NEW.status IN ('proved', 'refuted'))
    EXECUTE FUNCTION rm.guard_formalization_output();

REVOKE EXECUTE ON ALL ROUTINES IN SCHEMA rm FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.propose_formalization(jsonb, jsonb, uuid),
    rm.confirm_formalization(uuid, integer, text, text[], text, text),
    rm.resolve_clarification(uuid, text, text) TO rm_human;
GRANT EXECUTE ON FUNCTION rm.request_clarification(uuid, text, text, text) TO rm_scheduler;
GRANT SELECT ON rm.formalization_status, rm.formalization_clarification_status TO rm_human, rm_scheduler, rm_verifier;
