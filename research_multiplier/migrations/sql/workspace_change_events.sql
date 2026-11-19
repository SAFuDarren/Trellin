-- Keep the established event reader shape. A private transaction identifier
-- lets related rows share one refresh hint without suppressing typed events.
ALTER TABLE rm.workspace_events ADD COLUMN origin_xid xid8;
ALTER TABLE rm.workspace_events ALTER COLUMN origin_xid SET DEFAULT pg_current_xact_id();
CREATE INDEX workspace_changes_in_transaction
    ON rm.workspace_events (workspace_id, origin_xid)
    WHERE type = 'workspace.changed';

CREATE FUNCTION rm.capture_workspace_change_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE source record;
DECLARE target_workspace uuid;
DECLARE target_claim uuid;
DECLARE actor_user uuid;
DECLARE area text := TG_ARGV[0];
DECLARE details jsonb;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF OLD IS NOT DISTINCT FROM NEW THEN
            RETURN NULL;
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN
        source := OLD;
    ELSE
        source := NEW;
    END IF;

    SELECT b.workspace_id INTO STRICT target_workspace FROM current_workspace_binding() b;
    IF target_workspace IS NULL THEN
        RETURN NULL;
    END IF;

    -- Follow only relational IDs. No record bodies or caller-supplied names
    -- are read to construct the hint.
    CASE TG_ARGV[1]
    WHEN 'claim' THEN target_claim := source.claim_id;
    WHEN 'subject' THEN target_claim := source.id;
    WHEN 'packet' THEN
        SELECT p.claim_id INTO target_claim FROM formalization_packets p WHERE p.id = source.packet_id;
    WHEN 'statement' THEN
        SELECT s.claim_id INTO target_claim FROM lean_statements s WHERE s.id = source.statement_id;
    WHEN 'check' THEN
        SELECT s.claim_id INTO target_claim FROM lean_checks c
        JOIN lean_statements s ON s.id = c.statement_id WHERE c.id = source.check_id;
    WHEN 'evidence' THEN
        SELECT e.claim_id INTO target_claim FROM evidence e WHERE e.id = source.evidence_id;
    WHEN 'attempt' THEN
        SELECT a.claim_id INTO target_claim FROM attempts a WHERE a.id = source.attempt_id;
    WHEN 'guidance' THEN
        SELECT g.claim_id INTO target_claim FROM workbench_guidance g WHERE g.id = source.guidance_id;
    WHEN 'goal' THEN
        SELECT selected.claim_id INTO target_claim FROM research_goal_selections selected
        WHERE selected.goal_id = source.goal_id;
    WHEN 'condition' THEN
        SELECT selected.claim_id INTO target_claim FROM research_condition_requests requested
        JOIN research_goal_selections selected ON selected.goal_id = requested.goal_id
        WHERE requested.id = source.request_id;
    WHEN 'clarification' THEN
        SELECT p.claim_id INTO target_claim FROM clarification_requests requested
        JOIN formalization_packets p ON p.id = requested.packet_id WHERE requested.id = source.request_id;
    WHEN 'policy' THEN
        SELECT p.claim_id INTO target_claim FROM research_progress_policies p WHERE p.id = source.policy_id;
    ELSE
        NULL;
    END CASE;

    -- Scoped authenticated approvals already emitted their typed event in 0075.
    IF TG_TABLE_NAME = 'formalization_approvals' THEN
        IF target_claim IS NOT NULL AND source.user_id IS NOT NULL THEN
            RETURN NULL;
        END IF;
    END IF;
    IF is_local_human_session() THEN
        SELECT u.id INTO actor_user FROM current_local_user() u;
    END IF;

    details := jsonb_strip_nulls(jsonb_build_object('area', area, 'claim_id', target_claim));
    IF NOT EXISTS (
        SELECT FROM workspace_events e WHERE e.workspace_id = target_workspace
          AND e.type = 'workspace.changed' AND e.origin_xid = pg_current_xact_id()
          AND e.user_id IS NOT DISTINCT FROM actor_user AND e.data = details
    ) THEN
        PERFORM append_workspace_event('workspace.changed', clock_timestamp(), actor_user, 'workspace', NULL, details);
    END IF;
    RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION rm.capture_workspace_change_event() FROM PUBLIC;

-- Durable tables that directly change a read model. Relation-specific typed
-- event sources and tables written only as supporting records are checked
-- separately by the writer-coverage regression.
DO $$
DECLARE entry record;
BEGIN
    FOR entry IN SELECT * FROM (VALUES
        ('attempts',                             'activity',      'claim'),
        ('attempt_measurements',                 'activity',      'attempt'),
        ('lean_core_author_provenance',          'activity',      'evidence'),
        ('lean_lemma_model_proposals',           'activity',      ''),
        ('lean_search_provenance',               'activity',      'evidence'),
        ('research_discussion_archives',         'activity',      'goal'),
        ('workbench_guidance',                   'activity',      'claim'),
        ('workbench_guidance_submissions',       'activity',      'guidance'),
        ('clarification_requests',              'clarification', 'packet'),
        ('clarification_resolutions',           'clarification', 'clarification'),
        ('research_condition_requests',         'clarification', 'goal'),
        ('research_condition_rechecks',         'clarification', 'condition'),
        ('route_condition_local_derivations',   'clarification', 'condition'),
        ('target_clarifications',               'clarification', 'goal'),
        ('research_intake_drafts',              'intake',        ''),
        ('research_intake_confirmations',       'intake',        'goal'),
        ('research_targets',                   'intake',        ''),
        ('research_target_bindings',           'intake',        'claim'),
        ('research_goals',                     'intake',        ''),
        ('research_goal_approvals',            'intake',        'goal'),
        ('research_conjectures',               'intake',        'goal'),
        ('research_goal_selections',          'intake',        'claim'),
        ('definitions',                       'library',       ''),
        ('formal_item_versions',              'library',       ''),
        ('formal_item_public_releases',       'library',       ''),
        ('formal_lean_derivations',           'library',       'packet'),
        ('formal_certificate_derivations',    'library',       'packet'),
        ('lean_core_exports',                 'library',       'evidence'),
        ('literature_searches',               'library',       'claim'),
        ('literature_search_hits',            'library',       ''),
        ('literature_search_links',           'library',       ''),
        ('literature_sources',                'library',       'claim'),
        ('literature_source_confirmations',   'library',       ''),
        ('literature_vector_profiles',        'library',       ''),
        ('literature_vectors',                'library',       ''),
        ('novelty_reviews',                   'library',       'claim'),
        ('claim_definitions',                 'claim',         'claim'),
        ('claim_hypotheses',                  'claim',         'claim'),
        ('claim_conclusion',                  'claim',         'claim'),
        ('claim_edges',                       'claim',         ''),
        ('formalization_packets',             'proof',         'claim'),
        ('formalization_approvals',           'proof',         'packet'),
        ('formalpedia_application_reviews',   'proof',         'statement'),
        ('formalpedia_basis_reviews',         'proof',         'statement'),
        ('formalpedia_goal_provenance',       'proof',         'evidence'),
        ('formalpedia_lemma_provenance',      'proof',         ''),
        ('flow_derivation_records',          'proof',         'packet'),
        ('evidence_basis_use',                'proof',         'evidence'),
        ('lean_statements',                   'proof',         'claim'),
        ('lean_checks',                       'proof',         'statement'),
        ('lean_check_reviews',                 'proof',         'check'),
        ('lean_execution_links',              'proof',         'check'),
        ('lean_scope_observations',           'proof',         'statement'),
        ('lean_core_imports',                 'proof',         'packet'),
        ('lean_lake_baselines',               'proof',         'statement'),
        ('lean_progress_runtime_signals',    'proof',         'policy'),
        ('lean_reductions',                   'proof',         'statement'),
        ('lean_reduction_results',            'proof',         'evidence'),
        ('lean_search_budgets',               'proof',         'claim'),
        ('lean_search_budget_changes',        'proof',         'claim'),
        ('local_lean_lemmas',                 'proof',         'statement'),
        ('native_lean_replay_archives',        'proof',         'evidence'),
        ('private_lean_model_approvals',      'proof',         'statement'),
        ('packet_library_items',             'proof',         'packet'),
        ('proof_scope_extractions',          'proof',         'evidence'),
        ('research_plans',                   'proof',         ''),
        ('research_plan_steps',              'proof',         'claim'),
        ('research_plan_returns',            'proof',         ''),
        ('research_progress_policies',       'proof',         'claim'),
        ('research_understanding',           'proof',         'evidence'),
        ('verification_basis_use',            'proof',         'check'),
        ('verification_local_lemma_use',      'proof',         'check'),
        ('checker_monitor_policies',         'operations',    ''),
        ('checker_holds',                    'operations',    ''),
        ('checker_hold_releases',            'operations',    ''),
        ('evaluation_reports',               'operations',    ''),
        ('evaluation_approvals',             'operations',    ''),
        ('human_time_entries',               'operations',    'claim'),
        ('incident_regressions',             'operations',    ''),
        ('lean_benchmark_observations',      'operations',    ''),
        ('lean_eval_corpora',                 'operations',    ''),
        ('lean_eval_case_splits',            'operations',    ''),
        ('lean_rechecks',                    'operations',    ''),
        ('lean_recheck_attempts',            'operations',    ''),
        ('lean_route_attestations',          'operations',    ''),
        ('lean_route_events',                'operations',    ''),
        ('project_model_limits',             'operations',    ''),
        ('project_model_tariffs',            'operations',    ''),
        ('project_model_dispatches',         'operations',    ''),
        ('research_review_policies',         'operations',    ''),
        ('shadow_experiments',               'operations',    ''),
        ('shadow_live_trials',               'operations',    ''),
        ('shadow_route_events',              'operations',    ''),
        ('shadow_review_times',              'operations',    ''),
        ('verifier_reports',                  'operations',    ''),
        ('verifier_report_decisions',        'operations',    '')
    ) AS rule(relation, area, claim_source)
    LOOP
        EXECUTE format('CREATE CONSTRAINT TRIGGER publish_workspace_change AFTER INSERT OR UPDATE OR DELETE '
                       'ON rm.%I DEFERRABLE INITIALLY DEFERRED FOR EACH ROW '
                       'EXECUTE FUNCTION rm.capture_workspace_change_event(%L, %L)',
                       entry.relation, entry.area, entry.claim_source);
    END LOOP;
END $$;

-- Existing typed events cover the common paths on these relations. Capture
-- only the remaining changes, and ignore lease heartbeats and cursor counts.
CREATE CONSTRAINT TRIGGER publish_claim_entry_change AFTER INSERT OR DELETE ON rm.claims
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION rm.capture_workspace_change_event('claim', 'subject');
CREATE CONSTRAINT TRIGGER publish_claim_detail_change AFTER UPDATE ON rm.claims
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (
        OLD IS DISTINCT FROM NEW AND NOT (OLD.frozen_at IS NULL AND NEW.frozen_at IS NOT NULL)
        AND NOT (OLD.status = 'open' AND NEW.status IN ('proved', 'refuted'))
    )
    EXECUTE FUNCTION rm.capture_workspace_change_event('claim', 'subject');
CREATE CONSTRAINT TRIGGER publish_claim_reference_change AFTER UPDATE ON rm.claim_references
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (OLD.code IS DISTINCT FROM NEW.code)
    EXECUTE FUNCTION rm.capture_workspace_change_event('claim', 'claim');
CREATE CONSTRAINT TRIGGER publish_other_decision_change AFTER INSERT ON rm.decisions
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (
        NEW.action IN ('supersede', 'archive')
        OR (NEW.user_id IS NULL AND NEW.action IN ('approve', 'return'))
        OR (NEW.user_id IS NULL AND NEW.action = 'freeze' AND NEW.decided_by <> 'program')
    )
    EXECUTE FUNCTION rm.capture_workspace_change_event('claim', 'claim');
CREATE CONSTRAINT TRIGGER publish_other_job_entry AFTER INSERT ON rm.jobs
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.kind NOT IN ('prove', 'verify'))
    EXECUTE FUNCTION rm.capture_workspace_change_event('activity', 'claim');
CREATE CONSTRAINT TRIGGER publish_other_job_transition AFTER UPDATE ON rm.jobs
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (
        (NEW.kind NOT IN ('prove', 'verify') AND OLD.status IS DISTINCT FROM NEW.status)
        OR (OLD.status = NEW.status AND OLD.lease_generation IS DISTINCT FROM NEW.lease_generation)
        OR (OLD.status = 'queued' AND NEW.status = 'queued' AND OLD.ready_at IS DISTINCT FROM NEW.ready_at)
    )
    EXECUTE FUNCTION rm.capture_workspace_change_event('activity', 'claim');
CREATE CONSTRAINT TRIGGER publish_program_basis_approval AFTER INSERT ON rm.formal_item_approvals
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.user_id IS NULL)
    EXECUTE FUNCTION rm.capture_workspace_change_event('library', '');
CREATE CONSTRAINT TRIGGER publish_program_statement_approval AFTER INSERT ON rm.lean_statement_approvals
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.user_id IS NULL)
    EXECUTE FUNCTION rm.capture_workspace_change_event('proof', 'statement');
CREATE CONSTRAINT TRIGGER publish_program_progress_approval AFTER INSERT ON rm.research_progress_approvals
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.user_id IS NULL)
    EXECUTE FUNCTION rm.capture_workspace_change_event('proof', 'policy');
CREATE CONSTRAINT TRIGGER publish_program_progress_stop AFTER INSERT ON rm.research_progress_stops
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.user_id IS NULL)
    EXECUTE FUNCTION rm.capture_workspace_change_event('proof', 'policy');
