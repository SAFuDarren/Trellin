-- #297: a researcher withdraws a frozen claim that cannot proceed, optionally naming the claim that
-- replaces it, and can restore it later. The claim keeps its contract, evidence and decision history.
-- Withdrawn claims are `archived`, or `superseded` when a replacement is named; the project map and
-- claim lists already leave both out. A `restore` decision is the only way back to `open`.

DO $$
DECLARE constraint_name text;
BEGIN
    FOR constraint_name IN
        SELECT c.conname FROM pg_constraint c
        WHERE c.conrelid = 'rm.decisions'::regclass AND c.contype = 'c'
          AND pg_get_constraintdef(c.oid) LIKE '%''archive''%'
    LOOP
        EXECUTE format('ALTER TABLE rm.decisions DROP CONSTRAINT %I', constraint_name);
    END LOOP;
END $$;
ALTER TABLE rm.decisions ADD CONSTRAINT decisions_action_check
    CHECK (action IN ('freeze', 'approve', 'return', 'promote', 'supersede', 'archive', 'restore'));
ALTER TABLE rm.decisions ADD CONSTRAINT decisions_action_shape CHECK (
    (action = 'freeze' AND evidence_id IS NULL AND decided_by IN ('human', 'program')) OR
    (action IN ('approve', 'return') AND evidence_id IS NOT NULL AND decided_by = 'human') OR
    (action = 'promote' AND evidence_id IS NOT NULL AND decided_by = 'program') OR
    (action IN ('supersede', 'archive', 'restore') AND evidence_id IS NULL AND decided_by = 'human')
);

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
                     AND stale.id = OLD.decisive_evidence_id)) OR
        -- #297: only a recorded human restore decision returns a withdrawn claim to open.
        (OLD.status IN ('archived', 'superseded') AND NEW.status = 'open' AND OLD.decisive_evidence_id IS NULL
         AND NEW.decisive_evidence_id IS NULL
         AND (SELECT d.action FROM decisions d WHERE d.claim_id = OLD.id
              ORDER BY d.sequence_no DESC LIMIT 1) = 'restore')
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

CREATE FUNCTION rm.withdraw_claim(subject_id uuid, actor_ref text, reason text, replacement_id uuid DEFAULT NULL)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE subject claims;
DECLARE replacement claims;
BEGIN
    IF btrim(coalesce(actor_ref, '')) = '' OR btrim(coalesce(reason, '')) = '' THEN
        RAISE EXCEPTION 'withdrawing a claim needs an actor and a reason' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO subject FROM claims c WHERE c.id = subject_id FOR UPDATE;
    IF NOT FOUND OR subject.kind <> 'researcher_claim' OR subject.status <> 'open' THEN
        RAISE EXCEPTION 'only an open researcher claim can be withdrawn' USING ERRCODE = '23514';
    END IF;
    IF replacement_id IS NOT NULL THEN
        SELECT * INTO replacement FROM claims c WHERE c.id = replacement_id;
        IF NOT FOUND OR replacement.id = subject.id OR replacement.kind <> 'researcher_claim'
            OR replacement.frozen_at IS NULL OR replacement.status IN ('archived', 'superseded') THEN
            RAISE EXCEPTION 'the replacing claim must be another current frozen researcher claim'
                USING ERRCODE = '23514';
        END IF;
        INSERT INTO claim_edges (source_id, target_id, kind, justification)
        VALUES (replacement.id, subject.id, 'supersedes', reason)
        ON CONFLICT (source_id, target_id, kind) DO NOTHING;
    END IF;
    INSERT INTO decisions (claim_id, contract_sha256, action, decided_by, actor_ref, reason)
    VALUES (subject.id, subject.contract_sha256,
            CASE WHEN replacement_id IS NULL THEN 'archive' ELSE 'supersede' END, 'human', actor_ref, reason);
    UPDATE claims SET status = CASE WHEN replacement_id IS NULL THEN 'archived'::claim_status
                                    ELSE 'superseded'::claim_status END
    WHERE id = subject.id;
    RETURN subject.id;
END $$;

CREATE FUNCTION rm.restore_claim(subject_id uuid, actor_ref text, reason text)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE subject claims;
BEGIN
    IF btrim(coalesce(actor_ref, '')) = '' OR btrim(coalesce(reason, '')) = '' THEN
        RAISE EXCEPTION 'restoring a claim needs an actor and a reason' USING ERRCODE = '22023';
    END IF;
    SELECT * INTO subject FROM claims c WHERE c.id = subject_id FOR UPDATE;
    IF NOT FOUND OR subject.kind <> 'researcher_claim' OR subject.status NOT IN ('archived', 'superseded')
        OR subject.decisive_evidence_id IS NOT NULL THEN
        RAISE EXCEPTION 'only a withdrawn researcher claim can be restored' USING ERRCODE = '23514';
    END IF;
    -- The replacement link stays as history; the claim simply becomes current again.
    INSERT INTO decisions (claim_id, contract_sha256, action, decided_by, actor_ref, reason)
    VALUES (subject.id, subject.contract_sha256, 'restore', 'human', actor_ref, reason);
    UPDATE claims SET status = 'open' WHERE id = subject.id;
    RETURN subject.id;
END $$;

CREATE CONSTRAINT TRIGGER publish_claim_restore_decision AFTER INSERT ON rm.decisions
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.action = 'restore')
    EXECUTE FUNCTION rm.capture_workspace_change_event('claim', 'claim');

REVOKE EXECUTE ON FUNCTION rm.withdraw_claim(uuid, text, text, uuid), rm.restore_claim(uuid, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rm.withdraw_claim(uuid, text, text, uuid), rm.restore_claim(uuid, text, text) TO rm_human;
