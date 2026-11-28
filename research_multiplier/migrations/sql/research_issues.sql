-- Each row is one immutable offer. Device bindings stay outside the database.
CREATE TABLE rm.research_issue_offers (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES rm.local_users(id),
    direction jsonb NOT NULL,
    inputs jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
REVOKE ALL ON rm.research_issue_offers FROM PUBLIC, rm_human, rm_agent, rm_scheduler, rm_verifier;
CREATE INDEX research_issue_offers_by_author ON rm.research_issue_offers (user_id, created_at DESC, id);
CREATE TRIGGER research_issue_offers_append_only BEFORE UPDATE OR DELETE ON rm.research_issue_offers
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();
CREATE CONSTRAINT TRIGGER publish_research_issue AFTER INSERT ON rm.research_issue_offers
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
    EXECUTE FUNCTION rm.capture_workspace_change_event('activity', '');

CREATE FUNCTION rm.read_research_offers(target_workspace uuid, target_offer uuid DEFAULT NULL)
RETURNS TABLE (id uuid, direction jsonb, inputs jsonb, created_at timestamptz)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    PERFORM require_workspace_event_reader($1);
    RETURN QUERY SELECT o.id, o.direction, o.inputs, o.created_at FROM research_issue_offers o
        JOIN current_local_user() author ON author.id = o.user_id
        WHERE $2 IS NULL OR o.id = $2 ORDER BY o.created_at DESC, o.id
        LIMIT CASE WHEN $2 IS NULL THEN 100 ELSE 1 END;
END $$;

CREATE FUNCTION rm.offer_research_issue(target_workspace uuid, target_offer uuid, draft jsonb, pins jsonb)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE author_id uuid;
DECLARE prior research_issue_offers;
DECLARE field record;
DECLARE pin jsonb;
DECLARE note jsonb := draft->'proof_note';
DECLARE claim uuid;
DECLARE saved_text text;
BEGIN
    PERFORM require_workspace_event_reader(target_workspace);
    SELECT author.id INTO STRICT author_id FROM current_local_user() author;
    IF target_offer IS NULL OR jsonb_typeof(draft) IS DISTINCT FROM 'object'
       OR jsonb_typeof(pins) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'invalid research offer' USING ERRCODE = '22023';
    END IF;
    -- A client retry and a concurrent submission must compare against the same receipt.
    PERFORM pg_advisory_xact_lock(hashtextextended(target_offer::text, 9085));
    SELECT o.* INTO prior FROM research_issue_offers o WHERE o.id = target_offer;
    IF FOUND THEN
        IF prior.user_id <> author_id OR prior.direction <> draft OR prior.inputs <> pins THEN
            RAISE EXCEPTION 'research offer key conflict' USING ERRCODE = '23505';
        END IF;
        RETURN;
    END IF;

    IF NOT draft ?& ARRAY['title','deliverable','acceptance_criteria','required_capabilities','question','claim_id','proof_note']
       OR (SELECT count(*) FROM jsonb_object_keys(draft)) <> 7
       OR pg_column_size(draft) > 65536 OR jsonb_array_length(pins) > 16 THEN
        RAISE EXCEPTION 'invalid research offer' USING ERRCODE = '22023';
    END IF;
    FOR field IN SELECT * FROM (VALUES ('title',160), ('deliverable',2000),
        ('acceptance_criteria',4000), ('question',6000)) AS limits(name,maximum)
    LOOP
        IF jsonb_typeof(draft->field.name) <> 'string' OR length(draft->>field.name) NOT BETWEEN 1 AND field.maximum
           OR (draft->>field.name) !~ '[^[:space:]]' THEN
            RAISE EXCEPTION 'invalid research direction' USING ERRCODE = '22023';
        END IF;
    END LOOP;
    IF jsonb_typeof(draft->'required_capabilities') <> 'array' THEN
        RAISE EXCEPTION 'invalid research capabilities' USING ERRCODE = '22023';
    END IF;
    IF jsonb_array_length(draft->'required_capabilities') NOT BETWEEN 1 AND 16 OR EXISTS (
        SELECT FROM jsonb_array_elements(draft->'required_capabilities') c(value)
        GROUP BY c.value HAVING count(*) > 1 OR jsonb_typeof(c.value) <> 'string'
            OR length(c.value #>> '{}') > 64 OR (c.value #>> '{}') !~ '^[a-z][a-z0-9]*([.-][a-z0-9]+)*$'
    ) THEN
        RAISE EXCEPTION 'invalid research capabilities' USING ERRCODE = '22023';
    END IF;
    FOR pin IN SELECT value FROM jsonb_array_elements(pins)
    LOOP
        IF jsonb_typeof(pin) <> 'object' THEN
            RAISE EXCEPTION 'invalid research input' USING ERRCODE = '22023';
        END IF;
        IF NOT pin ?& ARRAY['source_id','reference_id','sha256'] OR (SELECT count(*) FROM jsonb_object_keys(pin)) <> 3
           OR pin->>'source_id' IS DISTINCT FROM target_workspace::text
           OR jsonb_typeof(pin->'reference_id') <> 'string' OR (pin->>'reference_id')::uuid::text <> pin->>'reference_id'
           OR jsonb_typeof(pin->'sha256') <> 'string' OR (pin->>'sha256') !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'invalid research input' USING ERRCODE = '22023';
        END IF;
    END LOOP;
    IF EXISTS (SELECT FROM jsonb_array_elements(pins) p GROUP BY p->>'reference_id' HAVING count(*) > 1) THEN
        RAISE EXCEPTION 'duplicate research input' USING ERRCODE = '22023';
    END IF;
    claim := (draft->>'claim_id')::uuid;
    IF claim IS NOT NULL AND NOT EXISTS (SELECT FROM claims c WHERE c.id = claim AND c.contract_sha256 IS NOT NULL) THEN
        RAISE EXCEPTION 'research source unavailable' USING ERRCODE = 'P0002';
    END IF;
    IF note <> 'null'::jsonb THEN
        IF jsonb_typeof(note) <> 'object' THEN
            RAISE EXCEPTION 'invalid proof note' USING ERRCODE = '22023';
        END IF;
        IF NOT note ?& ARRAY['claim_id','node_id','draft_revision'] OR (SELECT count(*) FROM jsonb_object_keys(note)) <> 3
           OR claim IS NULL OR (note->>'claim_id') IS DISTINCT FROM claim::text THEN
            RAISE EXCEPTION 'invalid proof note' USING ERRCODE = '22023';
        END IF;
        -- Serialize the final revision check with an editor's update/discard.
        SELECT d.text INTO saved_text FROM workbench_proof_drafts d
            WHERE d.user_id = author_id AND d.claim_id = claim AND d.node_id = note->>'node_id'
                AND d.revision = (note->>'draft_revision')::uuid FOR SHARE;
        -- Same whitespace code points as Python str.strip() in the preview.
        IF NOT FOUND OR btrim(saved_text, U&'\0009\000A\000B\000C\000D\001C\001D\001E\001F\0020\0085\00A0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\2028\2029\202F\205F\3000')
           IS DISTINCT FROM draft->>'question' THEN
            RAISE EXCEPTION 'research source changed' USING ERRCODE = '23514';
        END IF;
    END IF;
    INSERT INTO research_issue_offers(id, user_id, direction, inputs) VALUES(target_offer, author_id, draft, pins);
END $$;

REVOKE ALL ON FUNCTION rm.read_research_offers(uuid,uuid), rm.offer_research_issue(uuid,uuid,jsonb,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.read_research_offers(uuid,uuid), rm.offer_research_issue(uuid,uuid,jsonb,jsonb) TO rm_human;
