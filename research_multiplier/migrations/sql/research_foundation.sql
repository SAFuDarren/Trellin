-- Items and proof records are append-only. A changed input creates a new version
-- or invalidation event; historical approvals and receipts remain addressable.
CREATE TABLE rm.foundation_revision (
    singleton boolean PRIMARY KEY CHECK (singleton),
    revision bigint NOT NULL DEFAULT 0
);
INSERT INTO rm.foundation_revision (singleton) VALUES (true);

CREATE TABLE rm.formal_item_keys (
    logical_key text PRIMARY KEY CHECK (length(btrim(logical_key)) BETWEEN 1 AND 250),
    last_version integer NOT NULL DEFAULT 0 CHECK (last_version >= 0)
);

CREATE TABLE rm.formal_item_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    logical_key text NOT NULL REFERENCES rm.formal_item_keys(logical_key),
    version integer NOT NULL CHECK (version > 0),
    origin text NOT NULL CHECK (origin IN ('human', 'packet', 'lean', 'certificate')),
    body jsonb NOT NULL CHECK (jsonb_typeof(body) = 'object'),
    version_sha256 rm.sha256_hex NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (logical_key, version),
    UNIQUE (logical_key, version_sha256)
);

CREATE TABLE rm.formal_item_approvals (
    version_id uuid PRIMARY KEY REFERENCES rm.formal_item_versions(id),
    actor_ref text NOT NULL CHECK (btrim(actor_ref) <> ''),
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    authenticated_role name NOT NULL DEFAULT session_user,
    approved_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE rm.packet_basis_items (
    packet_id uuid NOT NULL REFERENCES rm.formalization_approvals(packet_id),
    item_label text NOT NULL,
    version_id uuid NOT NULL REFERENCES rm.formal_item_versions(id),
    PRIMARY KEY (packet_id, item_label),
    UNIQUE (packet_id, version_id)
);
CREATE INDEX packet_basis_item_version ON rm.packet_basis_items(version_id);

CREATE TABLE rm.packet_library_items (
    packet_id uuid NOT NULL REFERENCES rm.formalization_approvals(packet_id),
    version_id uuid NOT NULL REFERENCES rm.formal_item_approvals(version_id),
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    authenticated_role name NOT NULL DEFAULT session_user,
    linked_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (packet_id, version_id)
);
CREATE INDEX packet_library_item_version ON rm.packet_library_items(version_id);

CREATE TABLE rm.lean_execution_links (
    check_id uuid PRIMARY KEY REFERENCES rm.lean_checks(id),
    verification_attempt_id uuid NOT NULL UNIQUE REFERENCES rm.attempts(id),
    target text NOT NULL CHECK (target IN ('goal', 'upper_bound', 'attainment'))
);
CREATE TABLE rm.legacy_lean_review_revisions (
    check_id uuid PRIMARY KEY REFERENCES rm.lean_checks(id),
    revision bigint NOT NULL DEFAULT 0
);
CREATE TABLE rm.formal_lean_derivations (
    version_id uuid PRIMARY KEY REFERENCES rm.formal_item_versions(id),
    packet_id uuid NOT NULL REFERENCES rm.formalization_packets(id),
    check_id uuid NOT NULL UNIQUE REFERENCES rm.lean_execution_links(check_id)
);
CREATE INDEX formal_lean_derivation_packet ON rm.formal_lean_derivations(packet_id);

CREATE TABLE rm.formal_certificate_derivations (
    version_id uuid PRIMARY KEY REFERENCES rm.formal_item_versions(id),
    packet_id uuid NOT NULL REFERENCES rm.formalization_packets(id),
    derivation_id uuid NOT NULL UNIQUE REFERENCES rm.flow_derivation_records(id)
);
CREATE INDEX formal_certificate_derivation_packet ON rm.formal_certificate_derivations(packet_id);

ALTER TABLE rm.evidence ALTER COLUMN attempt_id DROP NOT NULL;
ALTER TABLE rm.evidence ADD COLUMN lean_check_id uuid UNIQUE REFERENCES rm.lean_checks(id);
ALTER TABLE rm.evidence ADD CONSTRAINT evidence_submission_reference CHECK (
    (lean_check_id IS NULL AND attempt_id IS NOT NULL) OR
    (lean_check_id IS NOT NULL AND kind = 'lean' AND attempt_id IS NULL)
);
CREATE UNIQUE INDEX deterministic_certificate_once ON rm.evidence (verification_attempt_id)
    WHERE kind = 'exact_certificate' AND verdict = 'verified' AND audit_mode = 'none';

CREATE TABLE rm.verification_basis_use (
    check_id uuid NOT NULL REFERENCES rm.lean_execution_links(check_id),
    version_id uuid NOT NULL REFERENCES rm.formal_item_versions(id),
    dependency_kind text NOT NULL CHECK (dependency_kind IN ('scope','actual')),
    PRIMARY KEY (check_id, version_id, dependency_kind)
);
CREATE INDEX verification_basis_use_version ON rm.verification_basis_use(version_id);

CREATE TABLE rm.evidence_basis_use (
    evidence_id uuid NOT NULL REFERENCES rm.evidence(id),
    version_id uuid NOT NULL REFERENCES rm.formal_item_versions(id),
    PRIMARY KEY (evidence_id, version_id)
);
CREATE INDEX evidence_basis_use_version ON rm.evidence_basis_use(version_id);

CREATE TABLE rm.formal_item_retirements (
    version_id uuid PRIMARY KEY REFERENCES rm.formal_item_versions(id),
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    authenticated_role name NOT NULL DEFAULT session_user,
    retired_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE rm.verification_input_retirements (
    input_kind text NOT NULL CHECK (input_kind IN ('verifier', 'environment', 'source')),
    sha256 rm.sha256_hex NOT NULL,
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    authenticated_role name NOT NULL DEFAULT session_user,
    retired_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (input_kind, sha256)
);

DO $$
DECLARE table_name text;
BEGIN
    FOR table_name IN SELECT unnest(ARRAY['formal_item_versions', 'formal_item_approvals',
        'packet_basis_items', 'packet_library_items', 'lean_execution_links',
        'formal_lean_derivations', 'formal_certificate_derivations', 'verification_basis_use', 'evidence_basis_use',
        'formal_item_retirements', 'verification_input_retirements']) LOOP
        EXECUTE format('CREATE TRIGGER foundation_append_only BEFORE UPDATE OR DELETE ON rm.%I '
                        'FOR EACH ROW EXECUTE FUNCTION rm.reject_mutation()', table_name);
    END LOOP;
END $$;

-- A real PostgreSQL row lock fences invalidation against concurrent publication,
-- including a transaction reading from an older repeatable-read snapshot.
CREATE FUNCTION rm.lock_live_foundation() RETURNS void
LANGUAGE sql SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT FROM foundation_revision WHERE singleton FOR SHARE
$$;
CREATE FUNCTION rm.advance_foundation() RETURNS void
LANGUAGE sql SET search_path = pg_catalog, rm, pg_temp AS $$
    UPDATE foundation_revision SET revision = revision + 1 WHERE singleton
$$;

CREATE FUNCTION rm.insert_formal_item(logical_key text, origin text, body jsonb)
RETURNS rm.formal_item_versions
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE saved formal_item_versions;
DECLARE counter integer;
DECLARE digest text;
BEGIN
    IF nullif(btrim(logical_key), '') IS NULL OR length(logical_key) > 250
        OR jsonb_typeof(body) IS DISTINCT FROM 'object'
        OR coalesce(body ->> 'kind', '') NOT IN ('goal', 'definition', 'hypothesis', 'theorem')
        OR (origin = 'human' AND jsonb_typeof(body -> 'source') = 'object' AND body #>> '{source,sha256}' IS NOT NULL
            AND body #>> '{source,sha256}' !~ '^[0-9a-f]{64}$') THEN
        RAISE EXCEPTION 'formal item needs a logical key, kind, and valid source revision'
            USING ERRCODE = '22023';
    END IF;
    INSERT INTO formal_item_keys (logical_key) VALUES (insert_formal_item.logical_key)
        ON CONFLICT ON CONSTRAINT formal_item_keys_pkey DO NOTHING;
    SELECT k.last_version INTO counter FROM formal_item_keys k
        WHERE k.logical_key = insert_formal_item.logical_key FOR UPDATE;
    digest := encode(sha256(convert_to(jsonb_build_object('origin', origin, 'body', body)::text, 'UTF8')), 'hex');
    SELECT * INTO saved FROM formal_item_versions v
        WHERE v.logical_key = insert_formal_item.logical_key AND v.version_sha256 = digest;
    IF FOUND THEN RETURN saved; END IF;
    UPDATE formal_item_keys k SET last_version = counter + 1
        WHERE k.logical_key = insert_formal_item.logical_key;
    INSERT INTO formal_item_versions (logical_key, version, origin, body, version_sha256)
        VALUES (logical_key, counter + 1, origin, body, digest) RETURNING * INTO saved;
    RETURN saved;
END $$;

CREATE FUNCTION rm.propose_formal_item(logical_key text, body jsonb) RETURNS rm.formal_item_versions
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF logical_key LIKE 'packet:%' OR logical_key LIKE 'lean:%' OR logical_key LIKE 'certificate:%'
        OR jsonb_typeof(body -> 'natural') IS DISTINCT FROM 'string'
        OR nullif(btrim(body ->> 'natural'), '') IS NULL
        OR jsonb_typeof(body -> 'formal') IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'human proposals require a statement and its exact formal meaning'
            USING ERRCODE = '22023';
    END IF;
    RETURN insert_formal_item(logical_key, 'human', body);
END $$;

CREATE FUNCTION rm.approve_formal_item(version_id uuid, expected_sha text, actor_ref text, reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE item formal_item_versions;
DECLARE prior formal_item_approvals;
BEGIN
    PERFORM advance_foundation();
    SELECT * INTO STRICT item FROM formal_item_versions v WHERE v.id = version_id;
    IF item.origin <> 'human' OR item.version_sha256 IS DISTINCT FROM expected_sha
        OR EXISTS (SELECT 1 FROM formal_item_retirements r WHERE r.version_id = item.id)
        OR nullif(btrim(actor_ref), '') IS NULL OR nullif(btrim(reason), '') IS NULL THEN
        RAISE EXCEPTION 'approval requires the selected version and human reason' USING ERRCODE = '23514';
    END IF;
    SELECT * INTO prior FROM formal_item_approvals a WHERE a.version_id = item.id;
    IF FOUND THEN
        IF ROW(prior.actor_ref, prior.reason, prior.authenticated_role)
            IS DISTINCT FROM ROW(actor_ref, reason, session_user) THEN
            RAISE EXCEPTION 'a different approval already exists for this item version' USING ERRCODE = '23514';
        END IF;
        RETURN item.id;
    END IF;
    IF EXISTS (SELECT 1 FROM formal_item_approvals a JOIN formal_item_versions v ON v.id = a.version_id
               WHERE v.logical_key = item.logical_key AND v.version > item.version) THEN
        RAISE EXCEPTION 'a newer approved formal item exists' USING ERRCODE = '23514';
    END IF;
    INSERT INTO formal_item_approvals (version_id, actor_ref, reason) VALUES (item.id, actor_ref, reason);
    INSERT INTO formal_item_retirements (version_id, reason)
        SELECT a.version_id, 'Superseded by approved version ' || item.version
        FROM formal_item_approvals a JOIN formal_item_versions v ON v.id = a.version_id
        WHERE v.logical_key = item.logical_key AND v.version < item.version
        ON CONFLICT DO NOTHING;
    RETURN item.id;
END $$;

CREATE FUNCTION rm.capture_packet_basis(packet_id uuid) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE packet formalization_packets;
DECLARE parent uuid;
DECLARE entry jsonb;
DECLARE payload jsonb;
DECLARE version_id uuid;
DECLARE key text;
BEGIN
    SELECT p.* INTO STRICT packet FROM formalization_packets p JOIN formalization_approvals a
        ON a.packet_id = p.id WHERE p.id = capture_packet_basis.packet_id;
    SELECT basis_packet_id INTO parent FROM exploration_goal_links WHERE goal_review_id = packet.review_id;
    FOR entry IN SELECT value FROM jsonb_array_elements(packet.packet -> 'items') LOOP
        payload := entry - ARRAY['id','status','question'];
        key := 'packet:' || packet.review_id || ':' ||
            encode(sha256(convert_to(entry ->> 'id', 'UTF8')), 'hex');
        SELECT b.version_id INTO version_id FROM packet_basis_items b JOIN formal_item_versions v
            ON v.id = b.version_id WHERE b.packet_id = parent AND b.item_label = entry ->> 'id' AND v.body = payload;
        IF NOT FOUND THEN
            version_id := (insert_formal_item(key, 'packet', payload)).id;
        END IF;
        INSERT INTO packet_basis_items (packet_id, item_label, version_id)
            VALUES (packet.id, entry ->> 'id', version_id) ON CONFLICT DO NOTHING;
    END LOOP;
END $$;

CREATE FUNCTION rm.capture_packet_basis_trigger() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    PERFORM capture_packet_basis(NEW.packet_id);
    RETURN NEW;
END $$;
CREATE TRIGGER populate_packet_basis AFTER INSERT ON rm.formalization_approvals
    FOR EACH ROW EXECUTE FUNCTION rm.capture_packet_basis_trigger();

-- Backfill authenticated packet approvals in order so chosen goals can retain
-- the already-approved exploration item IDs.
DO $$
DECLARE p record;
BEGIN
    FOR p IN SELECT packet_id FROM rm.formalization_approvals ORDER BY approved_at, packet_id LOOP
        PERFORM rm.capture_packet_basis(p.packet_id);
    END LOOP;
END $$;

CREATE FUNCTION rm.capture_certificate_derivation(derivation_id uuid) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE source flow_derivation_records;
DECLARE node formal_item_versions;
BEGIN
    SELECT * INTO STRICT source FROM flow_derivation_records d WHERE d.id = derivation_id;
    node := insert_formal_item('certificate:' || source.packet_id || ':' || source.item_key,
        'certificate', jsonb_build_object('kind', 'theorem', 'format', 'certificate', 'natural', source."natural",
            'formal', source.formal, 'source', jsonb_build_object('kind', 'exact_certificate',
                'derivation_id', source.id, 'verification_attempt_id', source.verification_attempt_id),
            'artifact', jsonb_build_object('uri', source.artifact_uri, 'sha256', source.artifact_sha256)));
    INSERT INTO formal_certificate_derivations (version_id, packet_id, derivation_id)
        VALUES (node.id, source.packet_id, source.id) ON CONFLICT DO NOTHING;
END $$;

CREATE FUNCTION rm.capture_certificate_derivation_trigger() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    PERFORM capture_certificate_derivation(NEW.id);
    RETURN NEW;
END $$;
CREATE TRIGGER capture_certificate_fact AFTER INSERT ON rm.flow_derivation_records
    FOR EACH ROW EXECUTE FUNCTION rm.capture_certificate_derivation_trigger();

DO $$
DECLARE d record;
BEGIN
    FOR d IN SELECT id FROM rm.flow_derivation_records ORDER BY created_at, id LOOP
        PERFORM rm.capture_certificate_derivation(d.id);
    END LOOP;
END $$;

CREATE VIEW rm.foundation_stale WITH (security_barrier = true) AS
    WITH RECURSIVE edges(parent_kind, parent_id, child_kind, child_id) AS (
        SELECT 'item', version_id, 'lean', check_id FROM rm.verification_basis_use
        UNION ALL SELECT 'item', basis.version_id, 'lean', legacy.id FROM rm.lean_checks legacy
            JOIN rm.lean_statements statement ON statement.id = legacy.statement_id
            JOIN rm.packet_basis_items basis ON basis.packet_id = statement.packet_id
            WHERE NOT EXISTS (SELECT 1 FROM rm.lean_execution_links execution WHERE execution.check_id = legacy.id)
        UNION ALL SELECT 'item', version_id, 'evidence', evidence_id FROM rm.evidence_basis_use
        UNION ALL SELECT 'lean', check_id, 'item', version_id FROM rm.formal_lean_derivations
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

CREATE VIEW rm.formal_item_status WITH (security_barrier = true) AS
    SELECT v.*, (a.version_id IS NOT NULL OR b.version_id IS NOT NULL OR derived.version_id IS NOT NULL
                 OR certificate.version_id IS NOT NULL) AS admitted,
        EXISTS (SELECT 1 FROM rm.foundation_stale stale WHERE stale.kind = 'item' AND stale.id = v.id) AS needs_recheck,
        coalesce(derived.packet_id, certificate.packet_id) AS derivation_packet_id, derived.check_id AS lean_check_id
    FROM rm.formal_item_versions v
    LEFT JOIN rm.formal_item_approvals a ON a.version_id = v.id
    LEFT JOIN LATERAL (SELECT version_id FROM rm.packet_basis_items WHERE version_id = v.id LIMIT 1) b ON true
    LEFT JOIN rm.formal_lean_derivations derived ON derived.version_id = v.id
    LEFT JOIN rm.formal_certificate_derivations certificate ON certificate.version_id = v.id;

CREATE FUNCTION rm.get_packet_basis(packet_id uuid)
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
    ) attached
    JOIN formal_item_status item ON item.id = attached.version_id AND item.admitted AND NOT item.needs_recheck
    LEFT JOIN packet_basis_items packet_item ON packet_item.packet_id = s.id AND packet_item.version_id = item.id
    WHERE s.id = $1 AND s.proof_ready
    ORDER BY item.logical_key, item.version
$$;

CREATE FUNCTION rm.attach_formal_item(packet_id uuid, version_id uuid, reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    PERFORM lock_live_foundation();
    IF nullif(btrim(reason), '') IS NULL
        OR NOT EXISTS (SELECT 1 FROM formalization_status s WHERE s.id = packet_id AND s.proof_ready)
        OR NOT EXISTS (SELECT 1 FROM formal_item_status s JOIN formal_item_approvals a ON a.version_id = s.id
                      WHERE s.id = attach_formal_item.version_id AND s.body ->> 'kind' IN ('definition', 'theorem')
                        AND NOT s.needs_recheck) THEN
        RAISE EXCEPTION 'only an already approved definition or theorem can be linked to this frozen packet'
            USING ERRCODE = '23514';
    END IF;
    INSERT INTO packet_library_items (packet_id, version_id, reason)
        VALUES (packet_id, version_id, reason) ON CONFLICT DO NOTHING;
    RETURN version_id;
END $$;

CREATE FUNCTION rm.retire_formal_item(version_id uuid, reason text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    PERFORM advance_foundation();
    INSERT INTO formal_item_retirements (version_id, reason)
        VALUES (version_id, reason) ON CONFLICT DO NOTHING;
END $$;

CREATE FUNCTION rm.retire_verification_input(input_kind text, revision_sha text, reason text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    PERFORM advance_foundation();
    INSERT INTO verification_input_retirements (input_kind, sha256, reason)
        VALUES (input_kind, revision_sha, reason) ON CONFLICT DO NOTHING;
END $$;

CREATE FUNCTION rm.require_dynamic_lean_job(job rm.jobs) RETURNS rm.lean_statements
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE statement lean_statements;
BEGIN
    IF job.kind <> 'verify'
        OR job.payload ->> 'lean_statement_id' IS NULL
        OR coalesce(job.payload ->> 'target', '') NOT IN ('goal', 'upper_bound', 'attainment')
        OR NOT lean_artifact_ref_valid(job.payload -> 'submission')
        OR jsonb_typeof(coalesce(job.payload -> 'basis_version_ids', '[]'::jsonb)) IS DISTINCT FROM 'array'
        OR job.payload #>> '{checker,name}' IS DISTINCT FROM 'rm-lean-candidate-audit'
        OR job.payload #>> '{checker,version}' IS DISTINCT FROM '1'
        OR NOT coalesce(job.payload ->> 'checker_revision' ~ '^[0-9a-f]{64}$', false)
        OR NOT coalesce(job.payload ->> 'verification_environment_sha256' ~ '^[0-9a-f]{64}$', false) THEN
        RAISE EXCEPTION 'Lean verification needs an approved statement, target, and submission'
            USING ERRCODE = '23514';
    END IF;
    statement := require_lean_review((job.payload ->> 'lean_statement_id')::uuid, true);
    IF statement.claim_id IS DISTINCT FROM job.claim_id
        OR statement.contract_sha256 IS DISTINCT FROM job.contract_sha256
        OR statement.statement_sha256 IS DISTINCT FROM job.payload ->> 'statement_sha256' THEN
        RAISE EXCEPTION 'Lean job does not match its frozen claim and reviewed statement'
            USING ERRCODE = '23514';
    END IF;
    PERFORM lock_live_foundation();
    PERFORM require_current_basis(statement.packet_id, coalesce(job.payload -> 'basis_version_ids', '[]'::jsonb));
    RETURN statement;
END $$;

CREATE OR REPLACE FUNCTION rm.guard_candidate_job() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF NEW.kind = 'verify' AND NEW.payload ? 'lean_statement_id' THEN
        PERFORM require_dynamic_lean_job(NEW);
        RETURN NEW;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM attempts a WHERE a.id = (NEW.payload ->> 'candidate_attempt_id')::uuid
          AND a.claim_id = NEW.claim_id AND a.contract_sha256 = NEW.contract_sha256
          AND a.role = 'prove' AND a.finding = 'exact_proof'
          AND a.artifact_uri = NEW.payload #>> '{candidate,uri}'
          AND a.artifact_sha256 = NEW.payload #>> '{candidate,sha256}'
    ) THEN
        RAISE EXCEPTION '% job requires the exact candidate attempt and artifact', NEW.kind USING ERRCODE = '23514';
    END IF;
    IF NEW.kind = 'verify' AND NOT EXISTS (
        SELECT 1 FROM claims c WHERE c.id = NEW.claim_id AND c.sensitivity = 'public'
          AND c.domain = 'fixed-directed-max-flow' AND NEW.payload ->> 'checker_name' = 'exact-flow-cut'
          AND NEW.payload ->> 'checker_version' = 'v0a-2'
          AND NEW.payload ->> 'checker_revision' ~ '^[0-9a-f]{64}$'
    ) THEN
        RAISE EXCEPTION 'verify job requires the supported domain checker and its revision' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER check_dynamic_lean_lease BEFORE UPDATE OF status, lease_generation ON rm.jobs
    FOR EACH ROW WHEN (NEW.kind = 'verify' AND NEW.payload ? 'lean_statement_id'
        AND NEW.status = 'running' AND OLD.lease_generation <> NEW.lease_generation)
    EXECUTE FUNCTION rm.guard_candidate_job();

CREATE FUNCTION rm.lean_analysis_valid(body jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT CASE WHEN jsonb_typeof(body #> '{dependencies,axioms}') = 'array'
        THEN coalesce(body #> '{dependencies,unauthorized_axioms}' = '[]'::jsonb
            AND body #> '{dependencies,unapproved}' = '[]'::jsonb
            AND body #> '{dependencies,unsafe}' = '[]'::jsonb
            AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(body #> '{dependencies,axioms}') a
                WHERE a NOT IN ('"propext"'::jsonb, '"Classical.choice"'::jsonb, '"Quot.sound"'::jsonb)), false)
        ELSE false END
$$;

CREATE FUNCTION rm.require_current_basis(packet_id uuid, version_ids jsonb) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF jsonb_typeof(version_ids) IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'verification requires actual basis version IDs' USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements_text(version_ids) ids(version_id)
        WHERE NOT EXISTS (SELECT 1 FROM get_packet_basis(packet_id) basis
                          WHERE basis.item_version_id::text = ids.version_id
                            AND basis.body ->> 'kind' <> 'goal')
    ) OR EXISTS (
        SELECT 1 FROM packet_basis_items b JOIN foundation_stale stale ON stale.kind = 'item'
            AND stale.id = b.version_id WHERE b.packet_id = require_current_basis.packet_id
    ) THEN
        RAISE EXCEPTION 'verification used an unapproved or stale item version' USING ERRCODE = '23514';
    END IF;
END $$;

CREATE OR REPLACE FUNCTION rm.guard_formalization_job() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE selected uuid;
BEGIN
    PERFORM require_formalization_ready(NEW.claim_id, CASE WHEN NEW.kind = 'prove' THEN NEW.payload END);
    SELECT p.id INTO selected FROM formalization_status p WHERE p.claim_id = NEW.claim_id AND p.proof_ready;
    IF FOUND THEN
        PERFORM lock_live_foundation();
        PERFORM require_current_basis(selected, '[]'::jsonb);
    END IF;
    RETURN NEW;
END $$;

-- Mechanical verification runs under the verifier role. Bind its result to the
-- exact successful attempt, submitted artifact, reviewed statement and actual inputs.
CREATE FUNCTION rm.store_lean_execution(statement_id uuid, expected_sha text, body jsonb, expected_target text)
RETURNS rm.lean_checks
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE statement lean_statements;
DECLARE attempt attempts;
DECLARE job jobs;
DECLARE prior lean_checks;
DECLARE saved lean_checks;
DECLARE digest text;
BEGIN
    SELECT * INTO attempt FROM attempts a WHERE a.id = (body ->> 'verification_attempt_id')::uuid;
    IF NOT FOUND OR attempt.role <> 'verify' OR attempt.finding <> 'exact_proof' THEN
        RAISE EXCEPTION 'a successful verifier attempt must precede a Lean result' USING ERRCODE = '23514';
    END IF;
    SELECT * INTO STRICT job FROM jobs j WHERE j.id = attempt.job_id;
    statement := require_dynamic_lean_job(job);
    PERFORM lock_live_foundation();
    IF job.status <> 'succeeded'
        OR statement.id IS DISTINCT FROM statement_id
        OR statement.statement_sha256 IS DISTINCT FROM expected_sha
        OR job.payload ->> 'target' IS DISTINCT FROM expected_target
        OR body ->> 'target' IS DISTINCT FROM expected_target
        OR body ->> 'theorem' IS DISTINCT FROM statement.body ->> 'theorem'
        OR (expected_target = 'goal' AND body ->> 'statement_type' IS DISTINCT FROM statement.body ->> 'theorem_type')
        OR body ->> 'candidate_theorem' IS DISTINCT FROM 'RMFlow.Submission.proof'
        OR body ->> 'trusted_source_sha256' IS DISTINCT FROM statement.body #>> '{trusted_source,sha256}'
        OR body ->> 'environment_sha256' IS DISTINCT FROM statement.body #>> '{environment,sha256}'
        OR body -> 'checker' IS DISTINCT FROM job.payload -> 'checker'
        OR body -> 'submission' IS DISTINCT FROM job.payload -> 'submission'
        OR body ->> 'checker_revision' IS DISTINCT FROM job.payload ->> 'checker_revision'
        OR body ->> 'verification_environment_sha256' IS DISTINCT FROM job.payload ->> 'verification_environment_sha256'
        OR NOT coalesce(coalesce(job.payload -> 'basis_version_ids', '[]'::jsonb) @> (body -> 'basis_version_ids'), false)
        OR nullif(btrim(body ->> 'statement_type'), '') IS NULL
        OR body -> 'compiled' IS DISTINCT FROM 'true'::jsonb
        OR body -> 'kernel_rechecked' IS DISTINCT FROM 'true'::jsonb
        OR body -> 'scope_matches' IS DISTINCT FROM 'true'::jsonb
        OR body -> 'accepted' IS DISTINCT FROM 'true'::jsonb
        OR NOT lean_artifact_ref_valid(body -> 'candidate')
        OR NOT lean_artifact_ref_valid(body -> 'report')
        OR body #>> '{report,uri}' IS DISTINCT FROM attempt.artifact_uri
        OR body #>> '{report,sha256}' IS DISTINCT FROM attempt.artifact_sha256
        OR NOT lean_analysis_valid(body) THEN
        RAISE EXCEPTION 'Lean result does not match the approved statement, submitted candidate and successful check'
            USING ERRCODE = '23514';
    END IF;
    PERFORM require_current_basis(statement.packet_id, body -> 'basis_version_ids');
    IF EXISTS (SELECT 1 FROM verification_input_retirements r
        WHERE (r.input_kind = 'verifier' AND r.sha256 = body ->> 'checker_revision')
          OR (r.input_kind = 'environment' AND r.sha256 IN (body ->> 'environment_sha256',
                                                            body ->> 'verification_environment_sha256'))
          OR (r.input_kind = 'source' AND r.sha256 IN (body ->> 'trusted_source_sha256',
                body #>> '{candidate,sha256}', body #>> '{submission,sha256}'))) THEN
        RAISE EXCEPTION 'Lean verification input requires recheck' USING ERRCODE = '23514';
    END IF;
    SELECT c.* INTO prior FROM lean_checks c JOIN lean_execution_links execution ON execution.check_id = c.id
        WHERE execution.verification_attempt_id = attempt.id;
    IF FOUND THEN
        IF prior.body IS DISTINCT FROM body THEN
            RAISE EXCEPTION 'verification attempt already has a different Lean result' USING ERRCODE = '23514';
        END IF;
        RETURN prior;
    END IF;

    digest := encode(sha256(convert_to(body::text, 'UTF8')), 'hex');
    INSERT INTO lean_checks (statement_id, statement_sha256, body, check_sha256)
        VALUES (statement.id, statement.statement_sha256, body, digest)
        ON CONFLICT ON CONSTRAINT lean_check_digest_unique DO NOTHING RETURNING * INTO saved;
    IF NOT FOUND THEN
        SELECT c.* INTO STRICT saved FROM lean_checks c
            WHERE c.statement_id = statement.id AND c.check_sha256 = digest;
    END IF;
    INSERT INTO lean_execution_links (check_id, verification_attempt_id, target)
        VALUES (saved.id, attempt.id, expected_target) ON CONFLICT (check_id) DO NOTHING;
    INSERT INTO verification_basis_use (check_id, version_id, dependency_kind)
        SELECT saved.id, b.version_id, 'scope' FROM packet_basis_items b WHERE b.packet_id = statement.packet_id
        UNION SELECT saved.id, version_id::uuid, 'actual'
              FROM jsonb_array_elements_text(body -> 'basis_version_ids') ids(version_id)
        ON CONFLICT DO NOTHING;
    RETURN saved;
END $$;

CREATE OR REPLACE FUNCTION rm.guard_audit() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE author attempts%ROWTYPE;
DECLARE auditor attempts%ROWTYPE;
BEGIN
    IF NEW.kind = 'lean' AND NEW.lean_check_id IS NOT NULL THEN RETURN NEW; END IF;
    SELECT * INTO author FROM attempts WHERE id = NEW.attempt_id;
    IF ROW(author.artifact_uri, author.artifact_sha256)
        IS DISTINCT FROM ROW(NEW.artifact_uri, NEW.artifact_sha256) THEN
        RAISE EXCEPTION 'evidence artifact must match the submitted attempt' USING ERRCODE = '23514';
    END IF;
    IF NEW.verdict = 'verified' AND NEW.kind = 'counterexample'
        AND author.finding <> 'counterexample' THEN
        RAISE EXCEPTION 'a verified counterexample needs a counterexample attempt' USING ERRCODE = '23514';
    END IF;
    IF NEW.audit_mode = 'none' THEN RETURN NEW; END IF;
    SELECT * INTO auditor FROM attempts WHERE id = NEW.audit_attempt_id;
    IF author.role IS DISTINCT FROM 'prove' OR auditor.role IS DISTINCT FROM 'audit'
        OR author.model_family IS NULL OR auditor.model_family IS NULL OR auditor.finding IS DISTINCT FROM 'audit_report'
        OR (SELECT payload ->> 'candidate_attempt_id' FROM jobs WHERE id = auditor.job_id)
            IS DISTINCT FROM author.id::text
        OR (NEW.audit_mode = 'cross_family' AND author.model_family = auditor.model_family)
        OR (NEW.audit_mode = 'restricted_same_family' AND (
            author.model_family <> auditor.model_family OR author.model_id = auditor.model_id
            OR (SELECT sensitivity FROM claims WHERE id = NEW.claim_id) IS DISTINCT FROM 'restricted'
        )) THEN
        RAISE EXCEPTION 'audit attempt does not satisfy model-family policy' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION rm.guard_evidence_grade() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    PERFORM require_formalization_ready(NEW.claim_id);
    PERFORM lock_live_foundation();
    IF EXISTS (SELECT 1 FROM verification_input_retirements r
        WHERE (r.input_kind = 'verifier' AND r.sha256 = NEW.code_revision)
           OR (r.input_kind = 'environment' AND r.sha256 = NEW.environment_sha256)
           OR (r.input_kind = 'source' AND r.sha256 = NEW.artifact_sha256)) THEN
        RAISE EXCEPTION 'verification input requires recheck' USING ERRCODE = '23514';
    END IF;
    IF NEW.kind = 'lean' THEN
        IF NEW.verdict <> 'verified' OR NEW.diff_status <> 'exact' OR NEW.audit_mode <> 'none' OR NOT EXISTS (
            SELECT 1 FROM lean_checks c JOIN lean_statement_status s ON s.id = c.statement_id
            LEFT JOIN lean_execution_links execution ON execution.check_id = c.id
            WHERE c.id = NEW.lean_check_id AND s.claim_id = NEW.claim_id
              AND s.contract_sha256 = NEW.contract_sha256 AND s.check_ready
              AND c.body -> 'accepted' = 'true'::jsonb AND c.body -> 'scope_matches' = 'true'::jsonb
              AND c.body #>> '{candidate,uri}' = NEW.artifact_uri AND c.body #>> '{candidate,sha256}' = NEW.artifact_sha256
              AND (execution.target = 'goal' OR execution.check_id IS NULL)
              AND execution.verification_attempt_id IS NOT DISTINCT FROM NEW.verification_attempt_id
              AND NOT EXISTS (SELECT 1 FROM foundation_stale stale WHERE stale.kind = 'lean' AND stale.id = c.id)
        ) THEN
            RAISE EXCEPTION 'Lean evidence must refer to an accepted full-goal check'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.verdict = 'verified' AND NEW.kind IN ('audited_proof', 'counterexample') THEN
        RAISE EXCEPTION '% verification is not enabled', NEW.kind USING ERRCODE = '23514';
    END IF;
    IF NEW.verdict = 'verified' AND NEW.kind = 'exact_certificate' THEN
        IF NEW.audit_mode NOT IN ('none', 'cross_family') OR NOT has_bound_verification(NEW, false) THEN
            RAISE EXCEPTION 'exact evidence requires a successful deterministic check bound to its candidate'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.diff_status <> 'exact' THEN
            RAISE EXCEPTION 'exact evidence must match the frozen statement' USING ERRCODE = '23514';
        END IF;
        IF EXISTS (
            SELECT 1 FROM formalization_packets p JOIN packet_basis_items b ON b.packet_id = p.id
            JOIN foundation_stale stale ON stale.kind = 'item' AND stale.id = b.version_id
            WHERE p.claim_id = NEW.claim_id
        ) THEN
            RAISE EXCEPTION 'the frozen premise basis requires recheck' USING ERRCODE = '23514';
        END IF;
        IF NEW.audit_mode = 'cross_family' AND NOT EXISTS (
            SELECT 1 FROM attempts auditor JOIN jobs j ON j.id = auditor.job_id
            JOIN attempts proof ON proof.id = NEW.attempt_id
            WHERE auditor.id = NEW.audit_attempt_id
              AND j.payload #>> '{candidate,uri}' = NEW.artifact_uri
              AND j.payload #>> '{candidate,sha256}' = NEW.artifact_sha256
              AND nullif(btrim(proof.model_family), '') IS NOT NULL
              AND nullif(btrim(auditor.model_family), '') IS NOT NULL
              AND lower(btrim(proof.model_family)) <> lower(btrim(auditor.model_family))
        ) THEN
            RAISE EXCEPTION 'evidence audit must identify the submitted proof artifact' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION rm.capture_evidence_basis() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF NEW.kind = 'exact_certificate' AND NEW.verdict = 'verified' THEN
        INSERT INTO evidence_basis_use (evidence_id, version_id)
            SELECT NEW.id, b.version_id FROM formalization_status p JOIN packet_basis_items b ON b.packet_id = p.id
            WHERE p.claim_id = NEW.claim_id AND p.proof_ready ON CONFLICT DO NOTHING;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER capture_confirmed_evidence_basis AFTER INSERT ON rm.evidence
    FOR EACH ROW EXECUTE FUNCTION rm.capture_evidence_basis();

INSERT INTO rm.evidence_basis_use (evidence_id, version_id)
    SELECT e.id, b.version_id FROM rm.evidence e JOIN rm.formalization_packets p ON p.claim_id = e.claim_id
        JOIN rm.packet_basis_items b ON b.packet_id = p.id
    WHERE e.kind = 'exact_certificate' AND e.verdict = 'verified'
    ON CONFLICT DO NOTHING;

-- Both legacy paths advance the same row before reading reviews or evidence.
-- A repeatable-read transaction with an older snapshot must retry its write.
CREATE FUNCTION rm.fence_legacy_lean_review(check_id uuid) RETURNS void
LANGUAGE sql SET search_path = pg_catalog, rm, pg_temp AS $$
    INSERT INTO legacy_lean_review_revisions (check_id) VALUES ($1)
    ON CONFLICT ON CONSTRAINT legacy_lean_review_revisions_pkey DO UPDATE
    SET revision = legacy_lean_review_revisions.revision + 1
$$;

CREATE FUNCTION rm.issue_lean_evidence(check_id uuid) RETURNS rm.evidence
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE checked lean_checks;
DECLARE execution lean_execution_links;
DECLARE statement lean_statements;
DECLARE saved evidence;
DECLARE digest text;
BEGIN
    SELECT * INTO STRICT checked FROM lean_checks c WHERE c.id = issue_lean_evidence.check_id;
    SELECT * INTO execution FROM lean_execution_links e WHERE e.check_id = checked.id;
    IF NOT FOUND THEN
        PERFORM fence_legacy_lean_review(checked.id);
    ELSIF execution.target <> 'goal' THEN
        RAISE EXCEPTION 'a derived result cannot be promoted as the complete claim' USING ERRCODE = '23514';
    END IF;
    statement := require_lean_review(checked.statement_id, true);
    PERFORM lock_live_foundation();
    IF EXISTS (SELECT 1 FROM foundation_stale stale WHERE stale.kind = 'lean' AND stale.id = checked.id) THEN
        RAISE EXCEPTION 'Lean verification requires recheck' USING ERRCODE = '23514';
    END IF;
    digest := encode(sha256(convert_to('lean:' || checked.id::text || ':' || checked.check_sha256, 'UTF8')), 'hex');
    INSERT INTO evidence (claim_id, contract_sha256, attempt_id, kind, verdict, diff_status, audit_mode,
        artifact_uri, artifact_sha256, verifier_name, verifier_version, code_revision, environment_sha256,
        result, verification_attempt_id, issuance_sha256, lean_check_id)
    VALUES (statement.claim_id, statement.contract_sha256, NULL, 'lean', 'verified', 'exact', 'none',
        checked.body #>> '{candidate,uri}', checked.body #>> '{candidate,sha256}',
        checked.body #>> '{checker,name}', checked.body #>> '{checker,version}',
        coalesce(checked.body ->> 'checker_revision', statement.body #>> '{environment,inputs,RMFlow/Audit.lean}',
                 statement.body #>> '{trusted_source,sha256}'),
        coalesce(checked.body ->> 'verification_environment_sha256', checked.body ->> 'environment_sha256'),
        jsonb_build_object('lean_check_id', checked.id, 'report', checked.body -> 'report'),
        execution.verification_attempt_id, digest, checked.id)
    ON CONFLICT (lean_check_id) DO NOTHING RETURNING * INTO saved;
    IF FOUND THEN
        -- A previously authenticated review of this exact legacy check can be
        -- reused when it becomes a canonical receipt.
        INSERT INTO decisions (claim_id, contract_sha256, evidence_id, action,
                               decided_by, actor_ref, authenticated_role, reason, decided_at)
            SELECT statement.claim_id, statement.contract_sha256, saved.id, review.action,
                'human', review.actor_ref, review.authenticated_role, review.reason, review.reviewed_at
            FROM lean_check_reviews review WHERE review.check_id = checked.id ORDER BY review.sequence_no;
    ELSE
        SELECT * INTO STRICT saved FROM evidence e WHERE e.lean_check_id = checked.id;
    END IF;
    RETURN saved;
END $$;

CREATE FUNCTION rm.record_verified_lean(statement_id uuid, expected_sha text, body jsonb) RETURNS rm.evidence
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE checked lean_checks;
BEGIN
    checked := store_lean_execution(statement_id, expected_sha, body, 'goal');
    RETURN issue_lean_evidence(checked.id);
END $$;

CREATE FUNCTION rm.record_lean_derivation(statement_id uuid, expected_sha text, body jsonb) RETURNS rm.formal_item_versions
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE statement lean_statements;
DECLARE checked lean_checks;
DECLARE node formal_item_versions;
DECLARE target_name text := body ->> 'target';
BEGIN
    IF coalesce(target_name, '') NOT IN ('upper_bound', 'attainment') THEN
        RAISE EXCEPTION 'only a checked proper subgoal is a reusable Lean derivation' USING ERRCODE = '23514';
    END IF;
    checked := store_lean_execution(statement_id, expected_sha, body, target_name);
    SELECT * INTO STRICT statement FROM lean_statements s WHERE s.id = checked.statement_id;
    node := insert_formal_item('lean:' || statement.packet_id || ':' || target_name,
        'lean', jsonb_build_object('kind', 'theorem', 'format', 'lean', 'scope', statement.body -> 'scope',
            'target', target_name, 'statement_type', body ->> 'statement_type', 'submission', body -> 'submission',
            'candidate', body -> 'candidate', 'source', jsonb_build_object('kind', 'lean', 'check_id', checked.id)));
    INSERT INTO formal_lean_derivations (version_id, packet_id, check_id)
        VALUES (node.id, statement.packet_id, checked.id) ON CONFLICT (version_id) DO NOTHING;
    RETURN node;
END $$;

CREATE OR REPLACE FUNCTION rm.can_promote_receipt(receipt rm.evidence) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog, rm, pg_temp AS $$
    SELECT NOT EXISTS (SELECT 1 FROM foundation_stale stale WHERE stale.kind = 'evidence' AND stale.id = receipt.id)
      AND (receipt.issuance_sha256 IS NOT NULL OR (
        receipt.kind = 'exact_certificate' AND receipt.audit_mode = 'cross_family'
        AND has_bound_verification(receipt, true)
        AND EXISTS (
            SELECT 1 FROM attempts a JOIN jobs j ON j.id = a.job_id
            WHERE a.id = receipt.audit_attempt_id AND a.harness_version = 'v0a-1'
              AND j.payload #>> '{candidate,uri}' = receipt.artifact_uri
              AND j.payload #>> '{candidate,sha256}' = receipt.artifact_sha256
        )))
$$;

CREATE OR REPLACE FUNCTION rm.review_evidence(evidence_id uuid, action text, actor_ref text, reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE receipt evidence;
DECLARE decision_id uuid;
BEGIN
    IF action IS NULL OR action NOT IN ('approve', 'return') THEN
        RAISE EXCEPTION 'review requires approve or return' USING ERRCODE = '23514';
    END IF;
    SELECT * INTO STRICT receipt FROM evidence WHERE id = review_evidence.evidence_id;
    UPDATE claims SET status = status WHERE id = receipt.claim_id AND status = 'open';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'review requires an open claim' USING ERRCODE = '23514';
    END IF;
    IF action = 'approve' THEN
        PERFORM require_formalization_ready(receipt.claim_id);
        PERFORM lock_live_foundation();
        IF receipt.verdict <> 'verified' OR receipt.diff_status <> 'exact' OR NOT can_promote_receipt(receipt) THEN
            RAISE EXCEPTION 'approval requires a verified receipt with a supported evidence path' USING ERRCODE = '23514';
        END IF;
    END IF;
    INSERT INTO decisions (claim_id, contract_sha256, evidence_id, action, decided_by, actor_ref, reason)
        VALUES (receipt.claim_id, receipt.contract_sha256, receipt.id, action, 'human', actor_ref, reason)
        RETURNING id INTO decision_id;
    RETURN decision_id;
END $$;

CREATE OR REPLACE FUNCTION rm.promote_claim(evidence_id uuid, reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE receipt evidence;
BEGIN
    SELECT * INTO STRICT receipt FROM evidence WHERE id = promote_claim.evidence_id;
    PERFORM 1 FROM claims WHERE id = receipt.claim_id AND status = 'open' FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'promotion requires an open claim' USING ERRCODE = '23514';
    END IF;
    PERFORM require_formalization_ready(receipt.claim_id);
    PERFORM lock_live_foundation();
    IF NOT can_promote_receipt(receipt) THEN
        RAISE EXCEPTION 'promotion requires a current verified receipt' USING ERRCODE = '23514';
    END IF;
    UPDATE claims SET status = CASE WHEN receipt.kind = 'counterexample' THEN 'refuted'::claim_status
                                    ELSE 'proved'::claim_status END,
        decisive_evidence_id = receipt.id WHERE id = receipt.claim_id;
    INSERT INTO decisions (claim_id, contract_sha256, evidence_id, action, decided_by, actor_ref, reason)
        VALUES (receipt.claim_id, receipt.contract_sha256, receipt.id, 'promote', 'program', session_user, reason);
    RETURN receipt.claim_id;
END $$;

CREATE VIEW rm.evidence_foundation_status WITH (security_barrier = true) AS
    SELECT e.*, EXISTS (SELECT 1 FROM rm.foundation_stale stale WHERE stale.kind = 'evidence'
                         AND stale.id = e.id) AS needs_recheck,
        CASE latest.action WHEN 'approve' THEN 'approved' WHEN 'return' THEN 'returned' ELSE 'pending' END
            AS review_status
    FROM rm.evidence e
    LEFT JOIN LATERAL (
        SELECT d.action FROM rm.decisions d WHERE d.evidence_id = e.id
            AND d.action IN ('approve', 'return') ORDER BY d.sequence_no DESC LIMIT 1
    ) latest ON true;

CREATE VIEW rm.claim_foundation_status WITH (security_barrier = true) AS
    SELECT c.id, c.domain, c.title, c.contract_sha256, c.decisive_evidence_id,
        CASE WHEN decisive.needs_recheck THEN 'needs_recheck' ELSE c.status::text END AS status
    FROM rm.claims c LEFT JOIN rm.evidence_foundation_status decisive ON decisive.id = c.decisive_evidence_id;

-- Preserve the old view column order; the added Lean check pointer is appended.
CREATE OR REPLACE VIEW rm.shared_evidence WITH (security_barrier = true) AS
    SELECT e.* FROM rm.evidence e JOIN rm.claims c ON c.id = e.claim_id
    JOIN rm.evidence_foundation_status basis_status ON basis_status.id = e.id
    WHERE e.verdict = 'verified' AND e.diff_status = 'exact' AND c.sensitivity = 'public'
      AND e.issuance_sha256 IS NOT NULL AND NOT basis_status.needs_recheck AND basis_status.review_status <> 'returned'
      AND NOT EXISTS (SELECT 1 FROM rm.formalization_status f WHERE f.claim_id = c.id AND NOT f.superseded
                      AND NOT f.proof_ready);

-- Expired historical numeric derivations remain in storage without being returned as a current basis.
CREATE OR REPLACE VIEW rm.verified_derivations WITH (security_barrier = true) AS
    SELECT d.* FROM rm.flow_derivation_records d
    JOIN rm.formalization_status p ON p.id = d.packet_id
    JOIN rm.attempts a ON a.id = d.verification_attempt_id JOIN rm.jobs j ON j.id = a.job_id
    WHERE p.proof_ready AND NOT EXISTS (
        SELECT 1 FROM rm.packet_basis_items b JOIN rm.foundation_stale stale ON stale.kind = 'item'
            AND stale.id = b.version_id WHERE b.packet_id = p.id
    ) AND NOT EXISTS (
        SELECT 1 FROM rm.verification_input_retirements r
        WHERE r.input_kind = 'verifier' AND r.sha256 = j.payload ->> 'checker_revision'
    ) AND NOT EXISTS (
        SELECT 1 FROM rm.evidence e JOIN rm.foundation_stale stale ON stale.kind = 'evidence' AND stale.id = e.id
        WHERE e.verification_attempt_id = d.verification_attempt_id
    );

CREATE OR REPLACE VIEW rm.lean_check_status WITH (security_barrier = true) AS
    SELECT c.*, s.claim_id, s.packet_id, s.contract_sha256, s."current",
        s.check_ready AND NOT EXISTS (
            SELECT 1 FROM rm.foundation_stale stale WHERE stale.kind = 'lean' AND stale.id = c.id
        ) AS check_ready,
        coalesce(evidence_status.review_status,
            CASE latest.action WHEN 'approve' THEN 'approved' WHEN 'return' THEN 'returned' ELSE 'pending' END)
            AS review_status,
        coalesce(canonical.actor_ref, latest.actor_ref) AS reviewed_by,
        coalesce(canonical.decided_at, latest.reviewed_at) AS reviewed_at,
        coalesce(canonical.reason, latest.reason) AS review_reason,
        evidence_status.id AS evidence_id,
        EXISTS (SELECT 1 FROM rm.foundation_stale stale WHERE stale.kind = 'lean' AND stale.id = c.id) AS needs_recheck
    FROM rm.lean_checks c JOIN rm.lean_statement_status s ON s.id = c.statement_id
    LEFT JOIN rm.evidence_foundation_status evidence_status ON evidence_status.lean_check_id = c.id
    LEFT JOIN LATERAL (
        SELECT d.actor_ref, d.decided_at, d.reason FROM rm.decisions d WHERE d.evidence_id = evidence_status.id
            AND d.action IN ('approve','return') ORDER BY d.sequence_no DESC LIMIT 1
    ) canonical ON true
    LEFT JOIN LATERAL (
        SELECT r.action, r.actor_ref, r.reviewed_at, r.reason FROM rm.lean_check_reviews r
        WHERE r.check_id = c.id ORDER BY r.sequence_no DESC LIMIT 1
    ) latest ON true;

CREATE OR REPLACE FUNCTION rm.review_lean_check(check_id uuid, action text, actor_ref text, reason text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE checked lean_checks;
DECLARE recent lean_check_reviews;
DECLARE review_id uuid;
DECLARE evidence_id uuid;
DECLARE execution lean_execution_links;
BEGIN
    IF action IS NULL OR action NOT IN ('approve', 'return')
        OR nullif(btrim(actor_ref), '') IS NULL OR nullif(btrim(reason), '') IS NULL THEN
        RAISE EXCEPTION 'Lean review requires approval or return, a researcher name, and a reason' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO STRICT checked FROM lean_checks c WHERE c.id = review_lean_check.check_id;
    SELECT * INTO execution FROM lean_execution_links e WHERE e.check_id = checked.id;
    IF NOT FOUND THEN
        PERFORM fence_legacy_lean_review(checked.id);
    ELSIF execution.target <> 'goal' THEN
        RAISE EXCEPTION 'review of a complete claim requires the full-goal verification' USING ERRCODE = '23514';
    END IF;
    SELECT e.id INTO evidence_id FROM evidence e WHERE e.lean_check_id = checked.id;
    IF FOUND THEN
        RETURN review_evidence(evidence_id, action, actor_ref, reason);
    END IF;
    IF action = 'approve' THEN
        PERFORM require_lean_review(checked.statement_id, true);
        PERFORM lock_live_foundation();
        IF EXISTS (SELECT 1 FROM foundation_stale stale WHERE stale.kind = 'lean' AND stale.id = checked.id) THEN
            RAISE EXCEPTION 'approval requires a current Lean verification' USING ERRCODE = '23514';
        END IF;
    END IF;
    SELECT * INTO recent FROM lean_check_reviews d WHERE d.check_id = checked.id ORDER BY d.sequence_no DESC LIMIT 1;
    IF FOUND AND recent.action = action AND recent.actor_ref = actor_ref
        AND recent.authenticated_role = session_user AND recent.reason = reason THEN
        RETURN recent.id;
    END IF;
    INSERT INTO lean_check_reviews (check_id, action, actor_ref, reason)
    VALUES (checked.id, action, actor_ref, reason) RETURNING id INTO review_id;
    RETURN review_id;
END $$;


CREATE FUNCTION rm.reopen_stale_claim(claim_id uuid, actor_ref text, reason text) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE subject claims;
BEGIN
    SELECT * INTO STRICT subject FROM claims c WHERE c.id = claim_id FOR UPDATE;
    PERFORM require_formalization_ready(subject.id);
    PERFORM lock_live_foundation();
    IF subject.status NOT IN ('proved', 'refuted') OR nullif(btrim(actor_ref), '') IS NULL
        OR nullif(btrim(reason), '') IS NULL OR NOT EXISTS (
        SELECT 1 FROM foundation_stale stale WHERE stale.kind = 'evidence' AND stale.id = subject.decisive_evidence_id
    ) THEN
        RAISE EXCEPTION 'reopening requires a reason and stale decisive evidence' USING ERRCODE = '23514';
    END IF;
    UPDATE claims SET status = 'open', decisive_evidence_id = NULL WHERE id = subject.id;
    INSERT INTO decisions (claim_id, contract_sha256, evidence_id, action, decided_by, actor_ref, reason)
        VALUES (subject.id, subject.contract_sha256, subject.decisive_evidence_id, 'return', 'human', actor_ref, reason);
    RETURN subject.id;
END $$;

CREATE OR REPLACE FUNCTION rm.guard_claim() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE snapshot jsonb;
DECLARE receipt evidence%ROWTYPE;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'draft' THEN
            RAISE EXCEPTION 'insert the draft before freezing it' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        IF OLD.frozen_at IS NOT NULL THEN
            RAISE EXCEPTION 'frozen claims cannot be deleted' USING ERRCODE = '23514';
        END IF;
        RETURN OLD;
    END IF;
    IF NEW.id <> OLD.id THEN
        RAISE EXCEPTION 'claim id cannot change' USING ERRCODE = '23514';
    END IF;
    IF OLD.frozen_at IS NOT NULL
        AND (to_jsonb(NEW) - ARRAY['status','decisive_evidence_id'])
            IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['status','decisive_evidence_id']) THEN
        RAISE EXCEPTION 'claim contract is frozen' USING ERRCODE = '23514';
    END IF;
    IF OLD.status = 'draft' AND NEW.status <> 'draft' THEN
        IF NEW.status <> 'open' OR NOT EXISTS (SELECT 1 FROM claim_conclusion WHERE claim_id = OLD.id) THEN
            RAISE EXCEPTION 'freezing needs a conclusion and open status' USING ERRCODE = '23514';
        END IF;
        snapshot := jsonb_build_object(
            'format_version', 1, 'claim_id', NEW.id, 'kind', NEW.kind, 'origin', NEW.origin,
            'domain', NEW.domain, 'title', NEW.title, 'statement', NEW.statement,
            'sensitivity', NEW.sensitivity, 'formal_skeleton', NEW.formal_skeleton,
            'small_case_spec', NEW.small_case_spec,
            'hypotheses', (SELECT coalesce(jsonb_agg(to_jsonb(h) - 'claim_id' ORDER BY h.label COLLATE "C"), '[]')
                           FROM claim_hypotheses h WHERE h.claim_id = NEW.id),
            'conclusion', (SELECT to_jsonb(c) - 'claim_id' FROM claim_conclusion c WHERE c.claim_id = NEW.id),
            'definition_ids', (SELECT coalesce(jsonb_agg(d.definition_id ORDER BY d.definition_id), '[]')
                               FROM claim_definitions d WHERE d.claim_id = NEW.id)
        );
        NEW.frozen_at := clock_timestamp();
        NEW.contract_snapshot := snapshot;
        NEW.contract_bytes := snapshot::text;
        NEW.contract_sha256 := encode(sha256(convert_to(NEW.contract_bytes, 'UTF8')), 'hex');
    END IF;
    IF OLD.frozen_at IS NOT NULL AND NEW.status <> OLD.status AND NOT (
        (OLD.status = 'open' AND NEW.status IN ('proved', 'refuted', 'superseded', 'archived')) OR
        (OLD.status IN ('proved', 'refuted', 'superseded') AND NEW.status IN ('superseded', 'archived')) OR
        (OLD.status IN ('proved', 'refuted') AND NEW.status = 'open' AND NEW.decisive_evidence_id IS NULL
         AND EXISTS (SELECT 1 FROM foundation_stale stale WHERE stale.kind = 'evidence'
                     AND stale.id = OLD.decisive_evidence_id))
    ) THEN
        RAISE EXCEPTION 'invalid claim status transition' USING ERRCODE = '23514';
    END IF;
    IF NEW.status IN ('proved', 'refuted')
        AND ROW(NEW.status, NEW.decisive_evidence_id)
            IS DISTINCT FROM ROW(OLD.status, OLD.decisive_evidence_id) THEN
        SELECT * INTO receipt FROM evidence e
            WHERE e.id = NEW.decisive_evidence_id AND e.claim_id = NEW.id
               AND e.contract_sha256 = NEW.contract_sha256;
        IF OLD.status <> 'open' THEN
            RAISE EXCEPTION 'only an open claim can enter a terminal verdict' USING ERRCODE = '23514';
        END IF;
        IF NOT FOUND OR receipt.verdict <> 'verified' OR receipt.diff_status <> 'exact'
            OR (NEW.status = 'proved' AND receipt.kind NOT IN ('exact_certificate', 'audited_proof', 'lean'))
            OR (NEW.status = 'refuted' AND receipt.kind <> 'counterexample')
            OR (SELECT d.action FROM decisions d WHERE d.evidence_id = receipt.id
                AND d.action IN ('approve', 'return') ORDER BY d.sequence_no DESC LIMIT 1)
               IS DISTINCT FROM 'approve' THEN
            RAISE EXCEPTION 'promotion needs matching verified evidence and human approval' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END
$$;
REVOKE EXECUTE ON ALL ROUTINES IN SCHEMA rm FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.propose_formal_item(text, jsonb), rm.approve_formal_item(uuid, text, text, text),
    rm.attach_formal_item(uuid, uuid, text), rm.retire_formal_item(uuid, text),
    rm.retire_verification_input(text, text, text), rm.reopen_stale_claim(uuid, text, text) TO rm_human;
GRANT EXECUTE ON FUNCTION rm.get_packet_basis(uuid) TO rm_human, rm_scheduler, rm_verifier;
GRANT EXECUTE ON FUNCTION rm.issue_lean_evidence(uuid), rm.record_verified_lean(uuid, text, jsonb),
    rm.record_lean_derivation(uuid, text, jsonb) TO rm_verifier;
GRANT SELECT ON rm.formal_item_status, rm.foundation_stale, rm.claim_foundation_status
    TO rm_human, rm_scheduler, rm_verifier;
GRANT SELECT ON rm.evidence_foundation_status, rm.verification_basis_use, rm.evidence_basis_use,
    rm.formal_item_retirements, rm.verification_input_retirements TO rm_human, rm_verifier;
