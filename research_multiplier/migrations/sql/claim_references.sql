-- Reference metadata lives outside frozen research contracts and receipts.
CREATE TABLE rm.claim_reference_aliases (
    code text COLLATE "C" PRIMARY KEY CHECK (code ~ '^[A-Z][A-Z0-9]{1,11}$'),
    claim_id uuid NOT NULL REFERENCES rm.claims(id),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    authenticated_role name NOT NULL DEFAULT session_user,
    UNIQUE (claim_id, code)
);
CREATE TABLE rm.claim_references (
    claim_id uuid PRIMARY KEY REFERENCES rm.claims(id),
    code text COLLATE "C" NOT NULL,
    last_ordinal integer NOT NULL DEFAULT 1 CHECK (last_ordinal > 0),
    FOREIGN KEY (claim_id, code) REFERENCES rm.claim_reference_aliases(claim_id, code)
);
CREATE TABLE rm.claim_node_numbers (
    claim_id uuid NOT NULL REFERENCES rm.claim_references(claim_id),
    node_key text COLLATE "C" NOT NULL CHECK (length(node_key) BETWEEN 1 AND 128),
    ordinal integer NOT NULL CHECK (ordinal > 0),
    PRIMARY KEY (claim_id, node_key),
    UNIQUE (claim_id, ordinal)
);
CREATE SEQUENCE rm.claim_reference_fallback AS bigint MAXVALUE 99999999999;

REVOKE ALL ON rm.claim_reference_aliases, rm.claim_references, rm.claim_node_numbers FROM PUBLIC;
REVOKE ALL ON SEQUENCE rm.claim_reference_fallback FROM PUBLIC;
CREATE TRIGGER claim_reference_aliases_append_only BEFORE UPDATE OR DELETE ON rm.claim_reference_aliases
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();
CREATE TRIGGER claim_node_numbers_append_only BEFORE UPDATE OR DELETE ON rm.claim_node_numbers
    FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation();

CREATE FUNCTION rm.ensure_claim_reference(subject uuid) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE candidate text;
BEGIN
    IF EXISTS (SELECT 1 FROM claim_references r WHERE r.claim_id = subject) THEN
        RETURN;
    END IF;
    -- Only initialization needs this lock. Ordinary allocations lock the claim's cursor row.
    PERFORM pg_advisory_xact_lock(hashtextextended(subject::text, 9042));
    IF EXISTS (SELECT 1 FROM claim_references r WHERE r.claim_id = subject) THEN
        RETURN;
    END IF;
    SELECT upper(left(string_agg(left(word, 1), '' ORDER BY position), 4) COLLATE "C") INTO candidate
    FROM claims c
    CROSS JOIN LATERAL regexp_split_to_table(c.title COLLATE "C", '[^A-Za-z0-9'']+')
        WITH ORDINALITY AS words(word, position)
    WHERE c.id = subject AND word COLLATE "C" ~ '^[A-Za-z]'
      AND lower(word COLLATE "C") NOT IN ('a', 'an', 'and', 'for', 'in', 'of', 'on', 'the', 'to', 'with');
    IF length(candidate) >= 2 THEN
        INSERT INTO claim_reference_aliases(code, claim_id) VALUES (candidate, subject) ON CONFLICT DO NOTHING;
    END IF;
    IF length(candidate) < 2 OR candidate IS NULL OR NOT FOUND THEN
        LOOP
            candidate := 'C' || nextval('rm.claim_reference_fallback'::regclass);
            INSERT INTO claim_reference_aliases(code, claim_id) VALUES (candidate, subject) ON CONFLICT DO NOTHING;
            EXIT WHEN FOUND;
        END LOOP;
    END IF;
    INSERT INTO claim_references(claim_id, code) VALUES (subject, candidate);
    INSERT INTO claim_node_numbers(claim_id, node_key, ordinal) VALUES (subject, 'goal:' || subject, 1);
END $$;

CREATE FUNCTION rm.remember_claim_node(subject uuid, node text) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE next_ordinal integer;
BEGIN
    IF subject IS NULL OR EXISTS (
        SELECT 1 FROM claim_node_numbers n WHERE n.claim_id = subject AND n.node_key = node
    ) THEN
        RETURN;
    END IF;
    PERFORM ensure_claim_reference(subject);
    PERFORM 1 FROM claim_references r WHERE r.claim_id = subject FOR UPDATE;
    IF EXISTS (SELECT 1 FROM claim_node_numbers n WHERE n.claim_id = subject AND n.node_key = node) THEN
        RETURN;
    END IF;
    UPDATE claim_references r SET last_ordinal = r.last_ordinal + 1
    WHERE r.claim_id = subject RETURNING r.last_ordinal INTO STRICT next_ordinal;
    INSERT INTO claim_node_numbers(claim_id, node_key, ordinal) VALUES (subject, node, next_ordinal);
END $$;

CREATE FUNCTION rm.claim_reference_snapshot(subject uuid)
RETURNS TABLE (code text, numbers jsonb)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT r.code, coalesce((SELECT jsonb_object_agg(n.node_key, n.ordinal)
                             FROM claim_node_numbers n WHERE n.claim_id = r.claim_id), '{}'::jsonb)
    FROM claim_references r WHERE r.claim_id = $1
$$;

CREATE FUNCTION rm.set_claim_code(subject uuid, new_code text) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE owner uuid;
BEGIN
    IF new_code IS NULL OR new_code COLLATE "C" !~ '^[A-Z][A-Z0-9]{1,11}$' THEN
        RAISE EXCEPTION 'invalid claim short code' USING ERRCODE = '22023';
    END IF;
    PERFORM 1 FROM claim_references r JOIN claims c ON c.id = r.claim_id
    WHERE r.claim_id = subject AND c.frozen_at IS NOT NULL FOR UPDATE OF r;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'unknown frozen claim' USING ERRCODE = 'P0002';
    END IF;
    INSERT INTO claim_reference_aliases(code, claim_id) VALUES (new_code, subject) ON CONFLICT DO NOTHING;
    SELECT a.claim_id INTO STRICT owner FROM claim_reference_aliases a WHERE a.code = new_code;
    IF owner IS DISTINCT FROM subject THEN
        RAISE EXCEPTION 'claim short code is already reserved' USING ERRCODE = '23505';
    END IF;
    UPDATE claim_references r SET code = new_code WHERE r.claim_id = subject AND r.code <> new_code;
    RETURN new_code;
END $$;

CREATE FUNCTION rm.resolve_claim_code(reference text)
RETURNS TABLE (claim_id uuid, node_key text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
    WITH parsed AS (
        SELECT regexp_match(upper(btrim($1) COLLATE "C"), '^([A-Z][A-Z0-9]{1,11})(?:-([0-9]{1,10}))?$') AS parts
    )
    SELECT a.claim_id, n.node_key
    FROM parsed p JOIN claim_reference_aliases a ON a.code = p.parts[1]
    LEFT JOIN claim_node_numbers n ON n.claim_id = a.claim_id AND n.ordinal = p.parts[2]::bigint
    WHERE p.parts[2] IS NULL OR n.node_key IS NOT NULL
$$;

CREATE FUNCTION rm.capture_claim_reference() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    PERFORM ensure_claim_reference(NEW.id);
    RETURN NEW;
END $$;

-- Adapters are attached to source records, so every writer (CLI, HTTP, or worker)
-- registers the same opaque node keys. They do not modify the source records.
CREATE FUNCTION rm.capture_packet_item_reference() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE subject uuid;
BEGIN
    SELECT p.claim_id INTO subject FROM formalization_packets p
    JOIN formal_item_versions i ON i.id = NEW.version_id AND i.body ->> 'kind' IS DISTINCT FROM 'goal'
    WHERE p.id = NEW.packet_id;
    PERFORM remember_claim_node(subject, 'item:' || NEW.version_id);
    RETURN NEW;
END $$;

CREATE FUNCTION rm.capture_statement_node_reference() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE subject uuid;
DECLARE position integer;
BEGIN
    SELECT s.claim_id INTO STRICT subject FROM lean_statements s WHERE s.id = NEW.statement_id;
    IF TG_ARGV[0] = 'obligation' THEN
        FOR position IN 1..cardinality(NEW.obligations) LOOP
            PERFORM remember_claim_node(subject, 'obligation:' || NEW.implication_id || ':' || position);
        END LOOP;
    ELSE
        PERFORM remember_claim_node(subject, TG_ARGV[0] || ':' || NEW.id);
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION rm.capture_checked_item_reference() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE subject uuid;
BEGIN
    IF TG_TABLE_NAME = 'local_lean_lemma_basis' THEN
        SELECT s.claim_id INTO STRICT subject
        FROM local_lean_lemmas l JOIN lean_statements s ON s.id = l.statement_id WHERE l.id = NEW.lemma_id;
    ELSE
        SELECT s.claim_id INTO STRICT subject
        FROM lean_checks c JOIN lean_statements s ON s.id = c.statement_id WHERE c.id = NEW.check_id;
    END IF;
    IF EXISTS (SELECT 1 FROM formal_item_versions i
               WHERE i.id = NEW.version_id AND i.body ->> 'kind' IS DISTINCT FROM 'goal') THEN
        PERFORM remember_claim_node(subject, 'item:' || NEW.version_id);
    END IF;
    RETURN NEW;
END $$;

REVOKE ALL ON FUNCTION rm.ensure_claim_reference(uuid), rm.remember_claim_node(uuid, text),
    rm.capture_claim_reference(), rm.capture_packet_item_reference(), rm.capture_statement_node_reference(),
    rm.capture_checked_item_reference() FROM PUBLIC;
REVOKE ALL ON FUNCTION rm.claim_reference_snapshot(uuid), rm.set_claim_code(uuid, text),
    rm.resolve_claim_code(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.claim_reference_snapshot(uuid), rm.set_claim_code(uuid, text),
    rm.resolve_claim_code(text) TO rm_human;

-- Keep the writer cutover atomic with backfill. These are append sources, not
-- the current graph views, so superseded and discharged records keep numbers.
LOCK TABLE rm.claims, rm.packet_basis_items, rm.packet_library_items, rm.formal_lean_derivations,
    rm.formal_certificate_derivations, rm.lean_core_imports, rm.local_lean_lemmas, rm.lean_checks,
    rm.lean_reductions, rm.local_lean_lemma_basis, rm.verification_basis_use IN SHARE ROW EXCLUSIVE MODE;

DO $$
DECLARE subject uuid;
BEGIN
    FOR subject IN SELECT id FROM rm.claims WHERE frozen_at IS NOT NULL ORDER BY created_at, id
    LOOP
        PERFORM rm.ensure_claim_reference(subject);
    END LOOP;
END $$;

WITH packet_items AS (
    SELECT b.packet_id, b.version_id, greatest(a.approved_at, v.created_at) AS recorded_at,
           coalesce(item.position, 0) AS position
    FROM rm.packet_basis_items b JOIN rm.formalization_approvals a ON a.packet_id = b.packet_id
    JOIN rm.formalization_packets p ON p.id = b.packet_id
    JOIN rm.formal_item_versions v ON v.id = b.version_id
    LEFT JOIN LATERAL jsonb_array_elements(p.packet -> 'items') WITH ORDINALITY AS item(body, position)
        ON item.body ->> 'id' = b.item_label
    UNION ALL SELECT l.packet_id, l.version_id, l.linked_at, 0 FROM rm.packet_library_items l
    UNION ALL SELECT i.packet_id, i.version_id, i.imported_at, 0 FROM rm.lean_core_imports i
    UNION ALL
    SELECT d.packet_id, d.version_id, i.created_at, 0
    FROM rm.formal_lean_derivations d JOIN rm.formal_item_versions i ON i.id = d.version_id
    UNION ALL
    SELECT d.packet_id, d.version_id, i.created_at, 0
    FROM rm.formal_certificate_derivations d JOIN rm.formal_item_versions i ON i.id = d.version_id
),
checked_items AS (
    SELECT l.statement_id, b.version_id, l.recorded_at
    FROM rm.local_lean_lemma_basis b JOIN rm.local_lean_lemmas l ON l.id = b.lemma_id
    UNION ALL
    SELECT c.statement_id, b.version_id, c.recorded_at
    FROM rm.verification_basis_use b JOIN rm.lean_checks c ON c.id = b.check_id
),
occurrences AS (
    SELECT p.claim_id, 'item:' || i.id AS node_key, b.recorded_at, b.position
    FROM packet_items b JOIN rm.formalization_packets p ON p.id = b.packet_id
    JOIN rm.formal_item_versions i ON i.id = b.version_id AND i.body ->> 'kind' IS DISTINCT FROM 'goal'
    UNION ALL
    SELECT s.claim_id, 'item:' || i.id, b.recorded_at, 0
    FROM checked_items b JOIN rm.lean_statements s ON s.id = b.statement_id
    JOIN rm.formal_item_versions i ON i.id = b.version_id AND i.body ->> 'kind' IS DISTINCT FROM 'goal'
    UNION ALL
    SELECT s.claim_id, 'local-lemma:' || l.id, l.recorded_at, 0
    FROM rm.local_lean_lemmas l JOIN rm.lean_statements s ON s.id = l.statement_id
    UNION ALL
    SELECT s.claim_id, 'lean:' || c.id, c.recorded_at, 0
    FROM rm.lean_checks c JOIN rm.lean_statements s ON s.id = c.statement_id
    UNION ALL
    SELECT s.claim_id, 'obligation:' || r.implication_id || ':' || obligation.position, r.recorded_at, obligation.position
    FROM rm.lean_reductions r JOIN rm.lean_statements s ON s.id = r.statement_id
    CROSS JOIN LATERAL unnest(r.obligations) WITH ORDINALITY AS obligation(proposition, position)
),
first_occurrence AS (
    SELECT DISTINCT ON (o.claim_id, o.node_key) o.claim_id, o.node_key, o.recorded_at AS first_at, o.position
    FROM occurrences o JOIN rm.claim_references r ON r.claim_id = o.claim_id
    ORDER BY o.claim_id, o.node_key, o.recorded_at, o.position
)
INSERT INTO rm.claim_node_numbers(claim_id, node_key, ordinal)
SELECT claim_id, node_key,
       (1 + row_number() OVER (PARTITION BY claim_id ORDER BY first_at, position, node_key COLLATE "C"))::integer
FROM first_occurrence;

UPDATE rm.claim_references r SET last_ordinal = latest.ordinal
FROM (SELECT claim_id, max(ordinal) AS ordinal FROM rm.claim_node_numbers GROUP BY claim_id) latest
WHERE r.claim_id = latest.claim_id;

CREATE TRIGGER capture_claim_reference AFTER UPDATE OF status ON rm.claims
    FOR EACH ROW WHEN (OLD.frozen_at IS NULL AND NEW.frozen_at IS NOT NULL)
    EXECUTE FUNCTION rm.capture_claim_reference();
CREATE TRIGGER capture_basis_reference AFTER INSERT ON rm.packet_basis_items
    FOR EACH ROW EXECUTE FUNCTION rm.capture_packet_item_reference();
CREATE TRIGGER capture_library_reference AFTER INSERT ON rm.packet_library_items
    FOR EACH ROW EXECUTE FUNCTION rm.capture_packet_item_reference();
CREATE TRIGGER capture_derived_item_reference AFTER INSERT ON rm.formal_lean_derivations
    FOR EACH ROW EXECUTE FUNCTION rm.capture_packet_item_reference();
CREATE TRIGGER capture_derived_item_reference AFTER INSERT ON rm.formal_certificate_derivations
    FOR EACH ROW EXECUTE FUNCTION rm.capture_packet_item_reference();
CREATE TRIGGER capture_import_reference AFTER INSERT ON rm.lean_core_imports
    FOR EACH ROW EXECUTE FUNCTION rm.capture_packet_item_reference();
CREATE TRIGGER capture_statement_node_reference AFTER INSERT ON rm.local_lean_lemmas
    FOR EACH ROW EXECUTE FUNCTION rm.capture_statement_node_reference('local-lemma');
CREATE TRIGGER capture_statement_node_reference AFTER INSERT ON rm.lean_checks
    FOR EACH ROW EXECUTE FUNCTION rm.capture_statement_node_reference('lean');
CREATE TRIGGER capture_statement_node_reference AFTER INSERT ON rm.lean_reductions
    FOR EACH ROW EXECUTE FUNCTION rm.capture_statement_node_reference('obligation');
CREATE TRIGGER capture_used_item_reference AFTER INSERT ON rm.local_lean_lemma_basis
    FOR EACH ROW EXECUTE FUNCTION rm.capture_checked_item_reference();
CREATE TRIGGER capture_used_item_reference AFTER INSERT ON rm.verification_basis_use
    FOR EACH ROW EXECUTE FUNCTION rm.capture_checked_item_reference();
