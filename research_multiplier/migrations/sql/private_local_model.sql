CREATE FUNCTION rm.private_model_identity_valid(identity jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog AS $$
    SELECT coalesce(
        jsonb_typeof($1) = 'object'
        AND jsonb_typeof($1 -> 'provider') = 'string'
        AND $1 ->> 'provider' ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$'
        AND jsonb_typeof($1 -> 'model') = 'string'
        AND length(btrim($1 ->> 'model')) BETWEEN 1 AND 255
        AND jsonb_typeof($1 -> 'config_sha256') = 'string'
        AND $1 ->> 'config_sha256' ~ '^[0-9a-f]{64}$'
        AND CASE $1 ->> 'transport'
            WHEN 'ollama-loopback' THEN
                $1 - ARRAY['transport','provider','model','endpoint','config_sha256'] = '{}'::jsonb
                AND $1 ->> 'provider' = 'ollama-native'
                AND jsonb_typeof($1 -> 'endpoint') = 'string'
                AND $1 ->> 'endpoint' ~ '^http://(127[.]0[.]0[.]1|\[::1\]):[1-9][0-9]{0,4}$'
                AND CASE WHEN $1 ->> 'endpoint' ~ ':[1-9][0-9]{0,4}$'
                         THEN substring($1 ->> 'endpoint' FROM ':([0-9]+)$')::integer <= 65535 ELSE false END
                AND $1 ->> 'model' ~ '^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,254}$'
                AND $1 ->> 'model' !~* '(^|[-:/])cloud($|[-:/])'
            WHEN 'installed-local' THEN
                $1 - ARRAY['transport','provider','model','factory','config_sha256'] = '{}'::jsonb
                AND $1 ->> 'provider' NOT IN (
                    'codex-cli','claude-code','openai','anthropic','deepseek','ollama','ollama-native',
                    'openai-compatible','anthropic-compatible'
                )
                AND jsonb_typeof($1 -> 'factory') = 'string'
                AND length($1 ->> 'factory') <= 255
                AND $1 ->> 'factory' ~ '^[A-Za-z_][A-Za-z0-9_]*([.][A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*$'
            ELSE false END, false)
$$;

CREATE FUNCTION rm.private_model_receipt_valid(metadata jsonb, identity jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT coalesce(private_model_identity_valid($2)
        AND $1 #> '{parameters,private_model}' = $2
        AND $1 ->> 'provider' = $2 ->> 'provider'
        AND regexp_replace($1 ->> 'id', ':latest$', '') = regexp_replace($2 ->> 'model', ':latest$', '')
        AND $1 #> '{parameters,model_id_reported}' = 'true'::jsonb, false)
$$;

CREATE FUNCTION rm.require_private_model_packet(packet_id uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF NOT EXISTS (
        SELECT FROM formalization_status packet
        JOIN claims origin ON origin.id = packet.claim_id
        JOIN claim_foundation_status claim ON claim.id = origin.id
        WHERE packet.id = $1 AND packet.proof_ready AND origin.domain = 'lean-core'
            AND origin.sensitivity = 'restricted' AND claim.status = 'open' AND NOT claim.interpretation_hold
    ) THEN
        RAISE EXCEPTION 'a private model needs the exact current reviewed restricted Lean scope'
            USING ERRCODE = '23514';
    END IF;
    PERFORM require_current_basis($1, '[]'::jsonb);
END $$;

CREATE TABLE rm.private_lean_model_approvals (
    statement_id uuid NOT NULL REFERENCES rm.lean_statements(id),
    identity_sha256 rm.sha256_hex NOT NULL,
    identity jsonb NOT NULL CHECK (rm.private_model_identity_valid(identity)),
    authenticated_role name NOT NULL DEFAULT session_user,
    approved_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (statement_id, identity_sha256)
);
CREATE TRIGGER private_lean_model_approvals_immutable BEFORE UPDATE OR DELETE ON rm.private_lean_model_approvals
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE FUNCTION rm.authorize_private_lean_model(statement_id uuid, identity jsonb) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE statement lean_statements;
DECLARE digest text;
BEGIN
    IF NOT private_model_identity_valid($2) THEN
        RAISE EXCEPTION 'private inference requires a trusted local model identity' USING ERRCODE = '23514';
    END IF;
    statement := require_lean_review($1, true);
    PERFORM require_private_model_packet(statement.packet_id);
    digest := encode(sha256(convert_to($2::text, 'UTF8')), 'hex');
    INSERT INTO private_lean_model_approvals(statement_id, identity_sha256, identity)
    VALUES ($1, digest, $2) ON CONFLICT ON CONSTRAINT private_lean_model_approvals_pkey DO NOTHING;
    RETURN digest;
END $$;

CREATE FUNCTION rm.propose_private_lean_progress(statement_id uuid, model_rounds integer, priority integer, identity jsonb)
RETURNS rm.research_progress_policies
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE anchor lean_statements;
DECLARE packet formalization_packets;
DECLARE settings jsonb;
DECLARE digest text;
DECLARE saved research_progress_policies;
BEGIN
    anchor := require_lean_review($1, true);
    SELECT * INTO STRICT packet FROM formalization_packets p WHERE p.id = anchor.packet_id;
    IF anchor.body ->> 'theorem' IS DISTINCT FROM 'RMTheory.expected'
        OR NOT coalesce($2 BETWEEN 1 AND 8, false)
        OR NOT coalesce($3 BETWEEN -100 AND 100, false)
        OR NOT private_model_identity_valid($4) THEN
        RAISE EXCEPTION 'private Lean advancement needs a confirmed statement, bounded rounds and exact local model'
            USING ERRCODE = '23514';
    END IF;
    PERFORM lock_live_foundation();
    PERFORM require_private_model_packet(packet.id);
    settings := jsonb_build_object('metric', 'exact-closure', 'actions', '[]'::jsonb, 'max_rounds', 0,
        'proof_timeout_seconds', 0, 'proof_max_attempts', 0, 'budget_seconds', 0, 'priority', $3,
        'lean', jsonb_build_object('statement_id', anchor.id, 'statement_sha256', anchor.statement_sha256,
                                  'model_rounds', $2, 'private_model', $4));
    digest := encode(sha256(convert_to(jsonb_build_object('version', 1, 'packet_id', packet.id,
        'claim_id', anchor.claim_id, 'packet_sha256', packet.packet_sha256, 'contract_sha256', anchor.contract_sha256,
        'policy', settings)::text, 'UTF8')), 'hex');
    INSERT INTO research_progress_policies (packet_id, packet_sha256, claim_id, contract_sha256, policy, policy_sha256)
    VALUES (packet.id, packet.packet_sha256, anchor.claim_id, anchor.contract_sha256, settings, digest)
    ON CONFLICT (policy_sha256) DO NOTHING RETURNING * INTO saved;
    IF NOT FOUND THEN
        SELECT * INTO STRICT saved FROM research_progress_policies p WHERE p.policy_sha256 = digest;
    END IF;
    RETURN saved;
END $$;

CREATE OR REPLACE FUNCTION rm.lean_progress_inputs_before_runtime_signal(policy_id uuid)
RETURNS TABLE (statement_id uuid, model_rounds integer, inputs_sha256 text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT anchor.id, (policy.policy #>> '{lean,model_rounds}')::integer,
        encode(sha256(convert_to(jsonb_build_array(anchor.statement_sha256, basis.ids,
            lemmas.ids, exports.ids, reductions.signals)::text, 'UTF8')), 'hex')
    FROM research_progress_status policy
    JOIN lean_statement_status anchor ON anchor.id::text = policy.policy #>> '{lean,statement_id}'
        AND anchor.statement_sha256 = policy.policy #>> '{lean,statement_sha256}' AND anchor.check_ready
    CROSS JOIN LATERAL (
        SELECT coalesce(jsonb_agg(b.item_version_id ORDER BY b.item_version_id), '[]'::jsonb) AS ids
        FROM get_packet_basis(policy.packet_id) b WHERE b.body ->> 'format' IS DISTINCT FROM 'lean-native-proof'
    ) basis
    CROSS JOIN LATERAL (
        SELECT coalesce(jsonb_agg(l.id ORDER BY l.id), '[]'::jsonb) AS ids
        FROM local_lean_lemma_status(anchor.id) l WHERE l.status = 'verified'
    ) lemmas
    CROSS JOIN LATERAL (
        SELECT coalesce(jsonb_agg(native.id ORDER BY native.id), '[]'::jsonb) AS ids
        FROM lean_search_native_theorems(policy.packet_id, (policy.policy #>> '{lean,model_rounds}')::integer > 0
                 AND policy.policy #> '{lean,private_model}' IS NULL) native
    ) exports
    CROSS JOIN LATERAL (
        SELECT coalesce(jsonb_agg(jsonb_build_array(r.implication_id, r.status) ORDER BY r.implication_id),
                        '[]'::jsonb) AS signals FROM lean_reduction_status(anchor.id) r
    ) reductions
    WHERE policy.id = $1 AND policy.policy ? 'lean' AND policy.state = 'awaiting_evidence'
$$;


REVOKE ALL ON FUNCTION rm.propose_private_lean_progress(uuid,integer,integer,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.propose_private_lean_progress(uuid,integer,integer,jsonb) TO rm_human;

CREATE OR REPLACE FUNCTION rm.guard_restricted_lean_model() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE scoped boolean;
DECLARE job_payload jsonb;
DECLARE model_calls integer;
DECLARE anchor lean_statements;
DECLARE route_identity jsonb;
BEGIN
    SELECT domain = 'lean-core' AND sensitivity = 'restricted' INTO scoped FROM claims WHERE id = NEW.claim_id;
    IF TG_TABLE_NAME = 'jobs' THEN
        IF TG_OP = 'UPDATE' AND NEW.status <> 'running' THEN RETURN NEW; END IF;
        job_payload := NEW.payload;
        model_calls := (NEW.budget ->> 'max_model_calls')::integer;
        IF NOT coalesce(job_payload ? 'private_model', false)
            AND (NOT coalesce(scoped, false) OR coalesce(model_calls, 0) = 0) THEN RETURN NEW; END IF;
    ELSE
        IF NOT coalesce(scoped, false) AND NOT coalesce(NEW.model_parameters ? 'private_model', false)
        THEN RETURN NEW; END IF;
        SELECT job.payload, (job.budget ->> 'max_model_calls')::integer INTO job_payload, model_calls
        FROM jobs job WHERE job.id = NEW.job_id;
        IF NEW.model_provider IS NULL AND NOT coalesce(job_payload ? 'private_model', false)
        THEN RETURN NEW; END IF;
    END IF;

    route_identity := job_payload -> 'private_model';
    IF NOT coalesce(scoped, false) OR model_calls IS DISTINCT FROM 1
        OR job_payload -> 'lean_author' IS DISTINCT FROM 'true'::jsonb
        OR job_payload -> 'lean_search' IS DISTINCT FROM 'true'::jsonb
        OR NOT private_model_identity_valid(route_identity) THEN
        RAISE EXCEPTION 'restricted Lean work requires explicitly authorized private-local model dispatch'
            USING ERRCODE = '23514';
    END IF;
    SELECT * INTO anchor FROM lean_statements statement
    WHERE statement.id::text = job_payload ->> 'author_statement_id' AND statement.claim_id = NEW.claim_id
      AND statement.statement_sha256 = job_payload ->> 'author_statement_sha256'
      AND statement.packet_id::text = job_payload ->> 'formalization_id';
    IF NOT FOUND OR (NOT EXISTS (
        SELECT FROM private_lean_model_approvals approval WHERE approval.statement_id = anchor.id
            AND approval.identity_sha256 = encode(sha256(convert_to(route_identity::text, 'UTF8')), 'hex')
            AND approval.identity = route_identity
    ) AND NOT EXISTS (
        SELECT FROM research_progress_status policy WHERE policy.claim_id = NEW.claim_id
          AND policy.packet_id = anchor.packet_id AND policy.state = 'awaiting_evidence'
          AND policy.policy #>> '{lean,statement_id}' = anchor.id::text
          AND policy.policy #>> '{lean,statement_sha256}' = anchor.statement_sha256
          AND policy.policy #> '{lean,private_model}' = route_identity
    )) THEN
        RAISE EXCEPTION 'the researcher must explicitly authorize this local model for the exact Lean statement'
            USING ERRCODE = '23514';
    END IF;
    PERFORM require_lean_review(anchor.id, true);
    PERFORM require_private_model_packet(anchor.packet_id);
    IF TG_TABLE_NAME = 'attempts' THEN
        IF NEW.finding NOT IN ('numeric_observation','no_result') OR NEW.transcript_sha256 IS NULL
            OR NOT private_model_receipt_valid(
                jsonb_build_object('provider', NEW.model_provider, 'id', NEW.model_id, 'parameters', NEW.model_parameters),
                route_identity
            ) THEN
            RAISE EXCEPTION 'private model attempt does not match the authorized local route' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER check_restricted_lean_model_job ON rm.jobs;
CREATE TRIGGER check_restricted_lean_model_job BEFORE INSERT OR UPDATE OF status ON rm.jobs
    FOR EACH ROW WHEN (NEW.budget ->> 'max_model_calls' <> '0' OR NEW.payload ? 'private_model')
    EXECUTE FUNCTION rm.guard_restricted_lean_model();
DROP TRIGGER check_restricted_lean_model_attempt ON rm.attempts;
CREATE TRIGGER check_restricted_lean_model_attempt BEFORE INSERT ON rm.attempts
    FOR EACH ROW WHEN (NEW.model_provider IS NOT NULL OR (NEW.role = 'prove' AND NEW.finding <> 'no_result'))
    EXECUTE FUNCTION rm.guard_restricted_lean_model();

CREATE FUNCTION rm.guard_private_conjecture_provenance() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE sensitivity text;
DECLARE metadata jsonb := NEW.body #> '{provenance,model}';
DECLARE identity jsonb := metadata #> '{parameters,private_model}';
BEGIN
    IF metadata IS NULL THEN RETURN NEW; END IF;
    SELECT goal.body ->> 'sensitivity' INTO STRICT sensitivity FROM research_goals goal WHERE goal.id = NEW.goal_id;
    IF (sensitivity = 'restricted' AND (
            NOT pg_has_role(session_user, 'rm_human', 'USAGE')
            OR NOT private_model_receipt_valid(metadata, identity)
            OR NOT lean_artifact_ref_valid(NEW.body #> '{provenance,transcript}')
        )) OR (sensitivity = 'public' AND identity IS NOT NULL)
    THEN
        RAISE EXCEPTION 'conjecture model provenance must match the frame disclosure and the explicitly local route'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER guard_private_conjecture_provenance BEFORE INSERT ON rm.research_conjectures
    FOR EACH ROW EXECUTE FUNCTION rm.guard_private_conjecture_provenance();

REVOKE ALL ON TABLE rm.private_lean_model_approvals FROM PUBLIC;
REVOKE ALL ON FUNCTION rm.private_model_identity_valid(jsonb), rm.private_model_receipt_valid(jsonb,jsonb),
    rm.require_private_model_packet(uuid), rm.authorize_private_lean_model(uuid,jsonb),
    rm.guard_private_conjecture_provenance() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.require_private_model_packet(uuid) TO rm_verifier;
GRANT EXECUTE ON FUNCTION rm.authorize_private_lean_model(uuid,jsonb) TO rm_human;

CREATE OR REPLACE FUNCTION rm.draft_research_intake(body jsonb) RETURNS rm.research_intake_drafts
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE compared jsonb;
DECLARE checksum text;
DECLARE saved research_intake_drafts;
DECLARE target research_targets;
BEGIN
    IF jsonb_typeof(body) IS DISTINCT FROM 'object'
        OR body - ARRAY['format_version','title','domain','objective','sensitivity','notes','model','items','target'] <> '{}'::jsonb
        OR body -> 'format_version' IS DISTINCT FROM '1'::jsonb
        OR jsonb_typeof(body -> 'title') IS DISTINCT FROM 'string'
        OR nullif(btrim(body ->> 'title'), '') IS NULL OR length(body ->> 'title') > 300
        OR jsonb_typeof(body -> 'domain') IS DISTINCT FROM 'string'
        OR nullif(btrim(body ->> 'domain'), '') IS NULL OR length(body ->> 'domain') > 100
        OR jsonb_typeof(body -> 'objective') IS DISTINCT FROM 'string'
        OR nullif(btrim(body ->> 'objective'), '') IS NULL OR length(body ->> 'objective') > 4000
        OR coalesce(body ->> 'sensitivity', '') NOT IN ('public','restricted')
        OR NOT lean_artifact_ref_valid(body -> 'notes')
        OR (body ? 'model' AND (
            jsonb_typeof(body -> 'model') IS DISTINCT FROM 'object'
            OR NOT lean_artifact_ref_valid(body #> '{model,transcript}')
            OR (body ->> 'sensitivity' = 'restricted' AND NOT private_model_receipt_valid(
                body #> '{model,call}', body #> '{model,call,parameters,private_model}'
            ))
            OR (body ->> 'sensitivity' = 'public' AND body #> '{model,call,parameters,private_model}' IS NOT NULL)
        ))
        OR jsonb_typeof(body -> 'items') IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'intake needs a scoped objective, notes archive and review items' USING ERRCODE = '22023';
    END IF;
    IF jsonb_array_length(body -> 'items') > 50 OR EXISTS (
        SELECT FROM jsonb_array_elements(body -> 'items') item
        WHERE jsonb_typeof(item) IS DISTINCT FROM 'object'
           OR item - ARRAY['id','kind','natural','formal','question','origin'] <> '{}'::jsonb
           OR NOT coalesce(item ->> 'id' ~ '^i[1-9][0-9]?$' , false)
           OR coalesce(item ->> 'kind','') NOT IN ('definition','hypothesis','theorem')
           OR coalesce(item ->> 'origin','') NOT IN ('notes','model')
           OR (item ->> 'origin' = 'model' AND NOT body ? 'model')
           OR jsonb_typeof(item -> 'natural') IS DISTINCT FROM 'string'
           OR nullif(btrim(item ->> 'natural'), '') IS NULL OR length(item ->> 'natural') > 4000
           OR jsonb_typeof(item -> 'formal') NOT IN ('object','null')
           OR NOT item ? 'formal'
           OR (item -> 'formal' <> 'null'::jsonb AND
               (item -> 'formal' = '{}'::jsonb OR pg_column_size(item -> 'formal') > 12000))
           OR (item ? 'question' AND (jsonb_typeof(item -> 'question') IS DISTINCT FROM 'string'
               OR nullif(btrim(item ->> 'question'), '') IS NULL OR length(item ->> 'question') > 2000))
    ) OR EXISTS (
        SELECT FROM jsonb_array_elements(body -> 'items') item GROUP BY item ->> 'id' HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'invalid intake review items' USING ERRCODE = '22023';
    END IF;
    IF body ? 'target' THEN
        IF jsonb_typeof(body -> 'target') IS DISTINCT FROM 'object'
            OR (body -> 'target') - ARRAY['id','sha256'] <> '{}'::jsonb
            OR NOT coalesce(body #>> '{target,id}' ~ '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$', false) THEN
            RAISE EXCEPTION 'select the exact frozen target for this intake' USING ERRCODE = '22023';
        END IF;
        SELECT * INTO target FROM research_targets t
        WHERE t.id = (draft_research_intake.body #>> '{target,id}')::uuid
          AND t.target_sha256 = draft_research_intake.body #>> '{target,sha256}';
        IF NOT FOUND OR body -> 'title' IS DISTINCT FROM target.body -> 'title'
            OR body -> 'domain' IS DISTINCT FROM target.body -> 'domain'
            OR body -> 'sensitivity' IS DISTINCT FROM target.body -> 'sensitivity' THEN
            RAISE EXCEPTION 'intake requires the exact frozen target and its scope' USING ERRCODE = '23514';
        END IF;
        IF EXISTS (SELECT FROM research_target_bindings b WHERE b.target_id = target.id) THEN
            RAISE EXCEPTION 'this target already has a confirmed starting basis' USING ERRCODE = '23514';
        END IF;
    END IF;
    SELECT coalesce(jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
        'id', item ->> 'id', 'match_version_id', matched,
        'question', item ->> 'question',
        'status', CASE WHEN target_as_basis(target.body, item) THEN 'omit_frozen_target'
                       WHEN item ? 'question' THEN 'clarify'
                       WHEN item ->> 'origin' = 'model' THEN 'confirm_scope'
                       WHEN matched IS NOT NULL THEN 'reuse'
                       WHEN item -> 'formal' = 'null'::jsonb THEN 'formalization_required'
                       ELSE 'confirm_meaning' END
    )) ORDER BY position), '[]'::jsonb)
    INTO compared
    FROM jsonb_array_elements(body -> 'items') WITH ORDINALITY AS entry(item, position)
    CROSS JOIN LATERAL (SELECT intake_exact_match(item) AS matched) exact;
    checksum := encode(sha256(convert_to(jsonb_build_object('body', body, 'comparison', compared)::text, 'UTF8')), 'hex');
    INSERT INTO research_intake_drafts (body, comparison, intake_sha256)
        VALUES (body, compared, checksum) ON CONFLICT (intake_sha256) DO NOTHING RETURNING * INTO saved;
    IF NOT FOUND THEN
        SELECT * INTO STRICT saved FROM research_intake_drafts WHERE intake_sha256 = checksum;
    END IF;
    RETURN saved;
END $$;

CREATE TABLE rm.research_discussion_archives (
    goal_id uuid NOT NULL REFERENCES rm.research_goals(id),
    frame_sha256 rm.sha256_hex NOT NULL,
    record_sha256 rm.sha256_hex NOT NULL,
    candidate_count integer NOT NULL CHECK (candidate_count BETWEEN 1 AND 4),
    authenticated_role name NOT NULL DEFAULT session_user,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (goal_id, record_sha256)
);
CREATE INDEX research_discussion_archive_latest
    ON rm.research_discussion_archives(goal_id, recorded_at DESC, record_sha256);
CREATE TRIGGER research_discussion_archives_immutable BEFORE UPDATE OR DELETE ON rm.research_discussion_archives
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE FUNCTION rm.remember_goal_discussion(goal_id uuid, frame_sha256 text, record_sha256 text, candidate_count integer)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    PERFORM 1 FROM research_goals goal WHERE goal.id = $1 FOR UPDATE;
    PERFORM lock_live_foundation();
    IF NOT EXISTS (
        SELECT FROM research_goal_status goal
        WHERE goal.id = $1 AND goal.frame_sha256 = $2 AND goal.approved_at IS NOT NULL
          AND goal.basis_current AND NOT goal.interpretation_hold AND goal.conjecture_id IS NULL
    ) THEN
        RAISE EXCEPTION 'a retained discussion requires the current reviewed research frame'
            USING ERRCODE = '23514';
    END IF;
    INSERT INTO research_discussion_archives (goal_id, frame_sha256, record_sha256, candidate_count)
    VALUES ($1, $2, $3, $4) ON CONFLICT ON CONSTRAINT research_discussion_archives_pkey DO NOTHING;
END $$;

CREATE VIEW rm.research_open_discussions WITH (security_barrier = true) AS
    SELECT DISTINCT ON (archive.goal_id) archive.*
    FROM rm.research_discussion_archives archive
    JOIN rm.research_goal_status goal ON goal.id = archive.goal_id AND goal.frame_sha256 = archive.frame_sha256
    WHERE goal.approved_at IS NOT NULL AND goal.basis_current
      AND NOT goal.interpretation_hold AND goal.conjecture_id IS NULL
    ORDER BY archive.goal_id, archive.recorded_at DESC, archive.record_sha256;

REVOKE ALL ON TABLE rm.research_discussion_archives, rm.research_open_discussions FROM PUBLIC;
REVOKE ALL ON FUNCTION rm.remember_goal_discussion(uuid,text,text,integer) FROM PUBLIC;
GRANT SELECT ON rm.research_open_discussions, rm.private_lean_model_approvals TO rm_human;
GRANT EXECUTE ON FUNCTION rm.remember_goal_discussion(uuid,text,text,integer) TO rm_human;
