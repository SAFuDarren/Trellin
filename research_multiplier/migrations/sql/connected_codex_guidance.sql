-- A per-note release may be recorded before signing a proof task. The release
-- does not start a worker or make any other content shareable.
CREATE FUNCTION rm.workbench_codex_release_available(anchor_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT EXISTS (
        SELECT FROM lean_statement_status s JOIN claims c ON c.id = s.claim_id
        WHERE s.id = $1 AND c.sensitivity = 'public' AND s."current" AND s.approved AND s.check_ready
            AND lean_model_packet_is_public(s.packet_id)
    )
$$;

REVOKE ALL ON FUNCTION rm.workbench_codex_release_available(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.workbench_codex_release_available(uuid) TO rm_human;

CREATE FUNCTION rm.require_connected_codex_guidance(job rm.jobs) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE hints jsonb := job.payload -> 'workbench_guidance_ids';
DECLARE anchor_id uuid := (job.payload ->> 'author_statement_id')::uuid;
BEGIN
    IF job.payload #>> '{public_model,provider}' IS DISTINCT FROM 'codex-cli'
        OR job.payload ->> 'workbench_guidance_model' IS DISTINCT FROM job.payload #>> '{public_model,model}'
        OR jsonb_typeof(hints) IS DISTINCT FROM 'array'
        OR NOT workbench_codex_release_available(anchor_id)
    THEN
        RAISE EXCEPTION 'connected guidance requires an exact public Codex release' USING ERRCODE = '23514';
    END IF;
    IF jsonb_array_length(hints) NOT BETWEEN 1 AND 3 OR (
        SELECT count(DISTINCT note.id)
        FROM jsonb_array_elements_text(hints) WITH ORDINALITY AS hint(id, position)
        JOIN workbench_guidance note ON note.id = CASE
            WHEN hint.id ~ '^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'
            THEN hint.id::uuid END
        WHERE note.claim_id = job.claim_id AND note.statement_id = anchor_id
            AND note.audience = 'codex' AND note.released_at <= statement_timestamp()
            AND (hint.position <> 1 OR note.node_id = job.payload ->> 'workbench_focus_node_id')
    ) <> jsonb_array_length(hints)
    THEN
        RAISE EXCEPTION 'connected guidance requires released notes for this exact statement' USING ERRCODE = '23514';
    END IF;
END $$;

REVOKE ALL ON FUNCTION rm.require_connected_codex_guidance(rm.jobs) FROM PUBLIC;

CREATE OR REPLACE FUNCTION rm.require_public_lean_policy_job(job rm.jobs, signed rm.research_progress_policies)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF signed.id IS NULL
        OR signed.claim_id IS DISTINCT FROM job.claim_id
        OR signed.contract_sha256 IS DISTINCT FROM job.contract_sha256
        OR job.kind IS DISTINCT FROM 'prove'
        OR job.budget -> 'max_model_calls' IS DISTINCT FROM '1'::jsonb
        OR job.payload -> 'lean_search' IS DISTINCT FROM 'true'::jsonb
        OR job.payload -> 'lean_author' IS DISTINCT FROM 'true'::jsonb
        OR job.payload ? 'private_model'
        OR signed.packet_id::text IS DISTINCT FROM job.payload ->> 'formalization_id'
        OR signed.packet_sha256 IS DISTINCT FROM job.payload ->> 'formalization_sha256'
        OR signed.policy #>> '{lean,statement_id}' IS DISTINCT FROM job.payload ->> 'author_statement_id'
        OR signed.policy #>> '{lean,statement_sha256}' IS DISTINCT FROM job.payload ->> 'author_statement_sha256'
        OR signed.policy #> '{lean,public_model}' IS NULL
        OR signed.policy #> '{lean,public_model}' IS DISTINCT FROM job.payload -> 'public_model'
        OR NOT public_lean_policy_in_workspace(signed.policy)
        OR NOT EXISTS (SELECT FROM research_progress_status p WHERE p.id = signed.id AND p.state = 'awaiting_evidence')
    THEN
        RAISE EXCEPTION 'a connected proof job needs its exact active signed model and scope' USING ERRCODE = '23514';
    END IF;
    PERFORM require_lean_review((job.payload ->> 'author_statement_id')::uuid, true);
    IF job.payload ?| ARRAY['workbench_guidance_ids', 'workbench_guidance_model', 'workbench_focus_node_id'] THEN
        PERFORM require_connected_codex_guidance(job);
    END IF;
END $$;
