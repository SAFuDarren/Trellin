-- A null user identifies a system result. Use the existing event shape and do
-- not put database logins, job owner strings or research text into the stream.
ALTER TABLE rm.workspace_events ALTER COLUMN user_id DROP NOT NULL;

CREATE FUNCTION rm.append_workspace_event(event_type text, occurred timestamptz, actor_user uuid,
                                        subject_type text, subject uuid, details jsonb) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE workspace uuid;
DECLARE event_id bigint;
BEGIN
    SELECT b.workspace_id INTO STRICT workspace FROM current_workspace_binding() b;
    IF workspace IS NULL THEN
        IF actor_user IS NOT NULL THEN
            RAISE EXCEPTION 'an attributed event requires a bound workspace' USING ERRCODE = '23514';
        END IF;
        RETURN;
    END IF;
    INSERT INTO workspace_event_heads AS head (workspace_id, last_id) VALUES (workspace, 1)
    ON CONFLICT (workspace_id) DO UPDATE SET last_id = head.last_id + 1
    RETURNING head.last_id INTO event_id;
    INSERT INTO workspace_events (
        workspace_id, id, type, schema_version, occurred_at, user_id, subject_kind, subject_id, data
    ) VALUES (
        workspace, event_id, event_type, 1, occurred, actor_user, subject_type,
        CASE WHEN subject_type = 'workspace' THEN workspace ELSE subject END,
        details
    );
    PERFORM pg_notify('rm_workspace_events', '');
END $$;

CREATE OR REPLACE FUNCTION rm.capture_evidence_review_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    PERFORM append_workspace_event('evidence.reviewed', NEW.decided_at, NEW.user_id, 'evidence', NEW.evidence_id,
        jsonb_build_object('decision_id', NEW.id, 'claim_id', NEW.claim_id, 'action', NEW.action));
    RETURN NEW;
END $$;

CREATE FUNCTION rm.capture_claim_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF NEW.action = 'freeze' THEN
        PERFORM append_workspace_event('claim.frozen', NEW.decided_at, NEW.user_id, 'claim', NEW.claim_id,
            jsonb_build_object('claim_id', NEW.claim_id));
    ELSE
        PERFORM append_workspace_event('claim.concluded', NEW.decided_at, NEW.user_id, 'claim', NEW.claim_id,
            jsonb_build_object('claim_id', NEW.claim_id, 'evidence_id', NEW.evidence_id));
    END IF;
    RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER publish_claim_event AFTER INSERT ON rm.decisions DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW WHEN (
        (NEW.action = 'freeze' AND (NEW.user_id IS NOT NULL OR NEW.decided_by = 'program')) OR NEW.action = 'promote'
    )
    EXECUTE FUNCTION rm.capture_claim_event();

CREATE FUNCTION rm.capture_scope_confirmation_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE claim uuid;
BEGIN
    IF TG_TABLE_NAME = 'formalization_approvals' THEN
        SELECT p.claim_id INTO STRICT claim FROM formalization_packets p WHERE p.id = NEW.packet_id;
        IF claim IS NOT NULL THEN
            PERFORM append_workspace_event('basis.confirmed', NEW.approved_at, NEW.user_id, 'formalization',
                NEW.packet_id, jsonb_build_object('claim_id', claim));
        END IF;
    ELSE
        SELECT s.claim_id INTO STRICT claim FROM lean_statements s WHERE s.id = NEW.statement_id;
        PERFORM append_workspace_event('lean_statement.confirmed', NEW.approved_at, NEW.user_id, 'lean_statement',
            NEW.statement_id, jsonb_build_object('claim_id', claim));
    END IF;
    RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER publish_basis_confirmation AFTER INSERT ON rm.formalization_approvals
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.user_id IS NOT NULL)
    EXECUTE FUNCTION rm.capture_scope_confirmation_event();
CREATE CONSTRAINT TRIGGER publish_lean_statement_confirmation AFTER INSERT ON rm.lean_statement_approvals
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.user_id IS NOT NULL)
    EXECUTE FUNCTION rm.capture_scope_confirmation_event();

CREATE FUNCTION rm.capture_basis_item_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    IF TG_TABLE_NAME = 'formal_item_approvals' THEN
        PERFORM append_workspace_event('basis_item.confirmed', NEW.approved_at, NEW.user_id, 'basis_item',
            NEW.version_id, '{}'::jsonb);
    ELSE
        PERFORM append_workspace_event('basis_item.retired', NEW.retired_at, NEW.user_id, 'basis_item',
            NEW.version_id, '{}'::jsonb);
    END IF;
    RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER publish_basis_item_confirmation AFTER INSERT ON rm.formal_item_approvals
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.user_id IS NOT NULL)
    EXECUTE FUNCTION rm.capture_basis_item_event();
CREATE CONSTRAINT TRIGGER publish_basis_item_retirement AFTER INSERT ON rm.formal_item_retirements
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
    EXECUTE FUNCTION rm.capture_basis_item_event();

CREATE FUNCTION rm.capture_verification_input_retirement() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    -- One input can affect several claims. Readers refresh existing assurance
    -- records; the event never carries the input digest or a duplicate closure.
    PERFORM append_workspace_event('verification_input.retired', NEW.retired_at, NEW.user_id, 'workspace',
        NULL, jsonb_build_object('input_kind', NEW.input_kind));
    RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER publish_verification_input_retirement AFTER INSERT ON rm.verification_input_retirements
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
    EXECUTE FUNCTION rm.capture_verification_input_retirement();

CREATE FUNCTION rm.capture_condition_decision_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE claim uuid;
BEGIN
    SELECT selected.claim_id INTO STRICT claim
    FROM research_condition_requests requested
    LEFT JOIN research_goal_selections selected ON selected.goal_id = requested.goal_id
    WHERE requested.id = NEW.request_id;
    PERFORM append_workspace_event('condition.decided', NEW.decided_at, NEW.user_id, 'condition', NEW.request_id,
        jsonb_build_object('claim_id', claim, 'action', NEW.action));
    RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER publish_condition_decision AFTER INSERT ON rm.research_condition_decisions
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
    EXECUTE FUNCTION rm.capture_condition_decision_event();

CREATE FUNCTION rm.capture_proof_task_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE target_policy_id uuid;
DECLARE claim uuid;
DECLARE event_user uuid;
DECLARE occurred timestamptz;
BEGIN
    IF TG_OP = 'DELETE' THEN
        target_policy_id := OLD.policy_id;
        occurred := clock_timestamp();
    ELSE
        target_policy_id := NEW.policy_id;
        CASE TG_TABLE_NAME
        WHEN 'research_progress_approvals' THEN
            event_user := NEW.user_id;
            occurred := NEW.approved_at;
        WHEN 'research_progress_stops' THEN
            event_user := NEW.user_id;
            occurred := NEW.stopped_at;
        WHEN 'research_progress_plans' THEN
            occurred := NEW.created_at;
        WHEN 'research_progress_observations', 'lean_progress_runs' THEN
            occurred := NEW.recorded_at;
        ELSE
            occurred := clock_timestamp();
        END CASE;
    END IF;
    SELECT p.claim_id INTO STRICT claim FROM research_progress_policies p WHERE p.id = target_policy_id;
    PERFORM append_workspace_event(TG_ARGV[0], occurred, event_user, 'proof_task', target_policy_id,
        jsonb_build_object('claim_id', claim));
    RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER publish_proof_task_authorization AFTER INSERT ON rm.research_progress_approvals
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.user_id IS NOT NULL)
    EXECUTE FUNCTION rm.capture_proof_task_event('proof_task.authorized');
CREATE CONSTRAINT TRIGGER publish_proof_task_stop AFTER INSERT ON rm.research_progress_stops
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.user_id IS NOT NULL)
    EXECUTE FUNCTION rm.capture_proof_task_event('proof_task.stopped');
CREATE CONSTRAINT TRIGGER publish_proof_task_started AFTER INSERT ON rm.lean_progress_worker_claims
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
    EXECUTE FUNCTION rm.capture_proof_task_event('proof_task.started');
CREATE CONSTRAINT TRIGGER publish_proof_task_restarted AFTER UPDATE ON rm.lean_progress_worker_claims
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (
        OLD.backend_pid IS DISTINCT FROM NEW.backend_pid OR OLD.inputs_sha256 IS DISTINCT FROM NEW.inputs_sha256
    )
    EXECUTE FUNCTION rm.capture_proof_task_event('proof_task.started');
CREATE CONSTRAINT TRIGGER publish_proof_task_released AFTER DELETE ON rm.lean_progress_worker_claims
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
    EXECUTE FUNCTION rm.capture_proof_task_event('proof_task.updated');
CREATE CONSTRAINT TRIGGER publish_proof_task_result AFTER INSERT ON rm.lean_progress_runs
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
    EXECUTE FUNCTION rm.capture_proof_task_event('proof_task.updated');
CREATE CONSTRAINT TRIGGER publish_proof_task_plan AFTER INSERT ON rm.research_progress_plans
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
    EXECUTE FUNCTION rm.capture_proof_task_event('proof_task.updated');
CREATE CONSTRAINT TRIGGER publish_proof_task_observation AFTER INSERT ON rm.research_progress_observations
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
    EXECUTE FUNCTION rm.capture_proof_task_event('proof_task.updated');

CREATE FUNCTION rm.capture_verification_job_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE event_user uuid;
DECLARE occurred timestamptz;
BEGIN
    IF TG_OP = 'INSERT' THEN
        occurred := NEW.enqueued_at;
    ELSE
        occurred := clock_timestamp();
        IF NEW.status = 'cancelled' THEN
            SELECT cancellation.user_id INTO event_user FROM job_cancellations cancellation
            WHERE cancellation.job_id = NEW.id;
        END IF;
    END IF;
    PERFORM append_workspace_event('job.status_changed', occurred, event_user, 'job', NEW.id,
        jsonb_build_object('claim_id', NEW.claim_id, 'status', NEW.status));
    RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER publish_verification_job_queue AFTER INSERT ON rm.jobs
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.kind IN ('prove', 'verify'))
    EXECUTE FUNCTION rm.capture_verification_job_event();
CREATE CONSTRAINT TRIGGER publish_verification_job_status AFTER UPDATE ON rm.jobs
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (
        NEW.kind IN ('prove', 'verify') AND OLD.status IS DISTINCT FROM NEW.status
    )
    EXECUTE FUNCTION rm.capture_verification_job_event();

CREATE FUNCTION rm.capture_recorded_evidence_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
BEGIN
    PERFORM append_workspace_event('evidence.recorded', NEW.issued_at, NULL, 'evidence', NEW.id,
        jsonb_build_object('claim_id', NEW.claim_id));
    RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER publish_recorded_evidence AFTER INSERT ON rm.evidence
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION rm.capture_recorded_evidence_event();

REVOKE ALL ON FUNCTION rm.append_workspace_event(text, timestamptz, uuid, text, uuid, jsonb),
    rm.capture_claim_event(), rm.capture_scope_confirmation_event(), rm.capture_basis_item_event(),
    rm.capture_verification_input_retirement(), rm.capture_condition_decision_event(), rm.capture_proof_task_event(),
    rm.capture_verification_job_event(), rm.capture_recorded_evidence_event() FROM PUBLIC;
