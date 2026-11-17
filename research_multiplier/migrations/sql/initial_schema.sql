-- Research Multiplier v2, initial Alembic schema.
CREATE SCHEMA rm;
SET LOCAL search_path = rm, pg_catalog;

CREATE DOMAIN sha256_hex AS text CHECK (VALUE ~ '^[a-f0-9]{64}$');
CREATE TYPE claim_kind AS ENUM ('researcher_claim', 'working_lemma');
CREATE TYPE claim_status AS ENUM ('draft', 'open', 'proved', 'refuted', 'superseded', 'archived');
CREATE TYPE edge_kind AS ENUM ('depends_on', 'implies', 'refutes', 'special_case_of',
    'weakens', 'generalizes', 'reduces_to', 'supersedes');
CREATE TYPE job_status AS ENUM ('queued', 'running', 'succeeded', 'failed', 'cancelled');
CREATE TYPE job_kind AS ENUM ('small_case', 'counterexample', 'prove', 'audit', 'verify');
CREATE TYPE finding_type AS ENUM ('exact_proof', 'conditional_proof', 'weaker_result',
    'special_case', 'counterexample', 'numeric_observation', 'audit_report', 'no_result');
CREATE TYPE evidence_kind AS ENUM ('numeric', 'exact_certificate', 'audited_proof', 'lean', 'counterexample');
CREATE TYPE scope_status AS ENUM ('exact', 'different', 'undetermined');
CREATE TYPE verdict AS ENUM ('verified', 'rejected', 'inconclusive');
CREATE TYPE actor_kind AS ENUM ('program', 'ai', 'human');

CREATE TABLE definitions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    domain text NOT NULL,
    name text NOT NULL,
    version text NOT NULL,
    statement text NOT NULL,
    lean_name text,
    implementation_uri text,
    implementation_sha256 sha256_hex,
    frozen_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (domain, name, version),
    CHECK ((implementation_uri IS NULL) = (implementation_sha256 IS NULL))
);

CREATE TABLE claims (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind claim_kind NOT NULL,
    origin actor_kind NOT NULL,
    domain text NOT NULL,
    title text NOT NULL,
    statement text NOT NULL CHECK (btrim(statement) <> ''),
    sensitivity text NOT NULL CHECK (sensitivity IN ('public', 'restricted')),
    formal_skeleton jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(formal_skeleton) = 'object'),
    small_case_spec jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(small_case_spec) = 'object'),
    revision bigint NOT NULL DEFAULT 0,
    status claim_status NOT NULL DEFAULT 'draft',
    frozen_at timestamptz,
    contract_snapshot jsonb,
    contract_bytes text,
    contract_sha256 sha256_hex,
    decisive_evidence_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (kind <> 'researcher_claim' OR origin = 'human'),
    CHECK ((status = 'draft') = (frozen_at IS NULL)),
    CHECK ((frozen_at IS NULL) = (contract_snapshot IS NULL)),
    CHECK ((frozen_at IS NULL) = (contract_bytes IS NULL)),
    CHECK ((frozen_at IS NULL) = (contract_sha256 IS NULL)),
    CHECK ((status IN ('proved', 'refuted')) = (decisive_evidence_id IS NOT NULL)),
    UNIQUE (id, contract_sha256)
);

CREATE TABLE claim_definitions (
    claim_id uuid NOT NULL REFERENCES claims(id),
    definition_id uuid NOT NULL REFERENCES definitions(id),
    PRIMARY KEY (claim_id, definition_id)
);

CREATE TABLE claim_hypotheses (
    claim_id uuid NOT NULL REFERENCES claims(id),
    label text NOT NULL CHECK (label ~ '^H[1-9][0-9]*$'),
    statement text NOT NULL CHECK (btrim(statement) <> ''),
    formal jsonb CHECK (formal IS NULL OR jsonb_typeof(formal) = 'object'),
    PRIMARY KEY (claim_id, label)
);

CREATE TABLE claim_conclusion (
    claim_id uuid PRIMARY KEY REFERENCES claims(id),
    statement text NOT NULL CHECK (btrim(statement) <> ''),
    quantifiers jsonb NOT NULL CHECK (jsonb_typeof(quantifiers) = 'array'),
    parameters jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(parameters) = 'array'),
    constant_dependencies jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(constant_dependencies) = 'object')
);

CREATE TABLE claim_edges (
    source_id uuid NOT NULL REFERENCES claims(id),
    target_id uuid NOT NULL REFERENCES claims(id),
    kind edge_kind NOT NULL,
    justification text,
    PRIMARY KEY (source_id, target_id, kind),
    CHECK (source_id <> target_id)
);

CREATE TABLE jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    claim_id uuid NOT NULL,
    contract_sha256 sha256_hex NOT NULL,
    kind job_kind NOT NULL,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    budget jsonb NOT NULL CHECK (jsonb_typeof(budget) = 'object'),
    priority integer NOT NULL DEFAULT 0,
    status job_status NOT NULL DEFAULT 'queued',
    max_attempts integer NOT NULL DEFAULT 1 CHECK (max_attempts > 0),
    lease_generation integer NOT NULL DEFAULT 0 CHECK (lease_generation >= 0),
    lease_owner text,
    lease_until timestamptz,
    checkpoint jsonb,
    enqueued_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (claim_id, contract_sha256) REFERENCES claims(id, contract_sha256),
    CHECK ((status = 'running') = (lease_owner IS NOT NULL AND lease_until IS NOT NULL)),
    CHECK ((lease_owner IS NULL) = (lease_until IS NULL)),
    CHECK (lease_generation <= max_attempts),
    UNIQUE (id, claim_id, contract_sha256)
);
CREATE INDEX jobs_ready ON jobs (kind, priority DESC, enqueued_at, id) WHERE status = 'queued';

CREATE TABLE attempts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id uuid NOT NULL,
    claim_id uuid NOT NULL,
    contract_sha256 sha256_hex NOT NULL,
    lease_generation integer NOT NULL CHECK (lease_generation > 0),
    submitted_by name NOT NULL,
    lease_owner text NOT NULL,
    submission_sha256 sha256_hex NOT NULL,
    role job_kind NOT NULL,
    route_key text,
    context_sha256 sha256_hex NOT NULL,
    harness_version text NOT NULL,
    model_provider text,
    model_id text,
    model_family text,
    model_revision text,
    model_parameters jsonb,
    prompt_sha256 sha256_hex,
    finding finding_type NOT NULL,
    artifact_uri text,
    artifact_sha256 sha256_hex,
    transcript_uri text,
    transcript_sha256 sha256_hex,
    failure_reason text,
    input_tokens bigint CHECK (input_tokens >= 0),
    output_tokens bigint CHECK (output_tokens >= 0),
    cost_usd numeric CHECK (cost_usd >= 0),
    submitted_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (job_id, claim_id, contract_sha256) REFERENCES jobs(id, claim_id, contract_sha256),
    UNIQUE (job_id, lease_generation),
    UNIQUE (id, claim_id, contract_sha256),
    CHECK ((artifact_uri IS NULL) = (artifact_sha256 IS NULL)),
    CHECK ((transcript_uri IS NULL) = (transcript_sha256 IS NULL)),
    CHECK (finding = 'no_result' OR artifact_uri IS NOT NULL),
    CHECK (finding <> 'audit_report' OR role = 'audit'),
    CHECK (role <> 'audit' OR finding IN ('audit_report', 'no_result')),
    CHECK (finding <> 'no_result' OR nullif(btrim(failure_reason), '') IS NOT NULL),
    CHECK ((model_provider IS NULL AND model_id IS NULL AND model_family IS NULL
            AND model_revision IS NULL AND model_parameters IS NULL AND prompt_sha256 IS NULL)
        OR (model_provider IS NOT NULL AND model_id IS NOT NULL
            AND model_family IS NOT NULL AND model_parameters IS NOT NULL AND prompt_sha256 IS NOT NULL))
);

CREATE TABLE evidence (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    claim_id uuid NOT NULL,
    contract_sha256 sha256_hex NOT NULL,
    attempt_id uuid NOT NULL,
    audit_attempt_id uuid,
    kind evidence_kind NOT NULL,
    verdict verdict NOT NULL,
    diff_status scope_status NOT NULL,
    audit_mode text NOT NULL DEFAULT 'none'
        CHECK (audit_mode IN ('none', 'cross_family', 'restricted_same_family')),
    artifact_uri text NOT NULL,
    artifact_sha256 sha256_hex NOT NULL,
    verifier_name text NOT NULL,
    verifier_version text NOT NULL,
    code_revision text NOT NULL,
    environment_sha256 sha256_hex NOT NULL,
    result jsonb NOT NULL CHECK (jsonb_typeof(result) = 'object'),
    issued_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (attempt_id, claim_id, contract_sha256) REFERENCES attempts(id, claim_id, contract_sha256),
    FOREIGN KEY (audit_attempt_id, claim_id, contract_sha256) REFERENCES attempts(id, claim_id, contract_sha256),
    UNIQUE (id, claim_id, contract_sha256),
    CHECK ((audit_mode = 'none') = (audit_attempt_id IS NULL)),
    CHECK (audit_attempt_id IS NULL OR audit_attempt_id <> attempt_id),
    CHECK (kind <> 'audited_proof' OR verdict <> 'verified' OR audit_attempt_id IS NOT NULL)
);
ALTER TABLE claims ADD FOREIGN KEY (decisive_evidence_id, id, contract_sha256)
    REFERENCES evidence(id, claim_id, contract_sha256);

CREATE TABLE decisions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    claim_id uuid NOT NULL,
    contract_sha256 sha256_hex NOT NULL,
    evidence_id uuid,
    action text NOT NULL CHECK (action IN ('freeze', 'approve', 'return', 'promote', 'supersede', 'archive')),
    decided_by actor_kind NOT NULL,
    actor_ref text NOT NULL CHECK (btrim(actor_ref) <> ''),
    authenticated_role name NOT NULL DEFAULT session_user,
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    sequence_no bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    decided_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (claim_id, contract_sha256) REFERENCES claims(id, contract_sha256),
    FOREIGN KEY (evidence_id, claim_id, contract_sha256) REFERENCES evidence(id, claim_id, contract_sha256),
    CHECK (
        (action = 'freeze' AND evidence_id IS NULL AND decided_by IN ('human', 'program')) OR
        (action IN ('approve', 'return') AND evidence_id IS NOT NULL AND decided_by = 'human') OR
        (action = 'promote' AND evidence_id IS NOT NULL AND decided_by = 'program') OR
        (action IN ('supersede', 'archive') AND evidence_id IS NULL AND decided_by = 'human')
    )
);
CREATE INDEX decisions_evidence_history ON decisions (evidence_id, sequence_no DESC);

CREATE TABLE harness_components (
    name text NOT NULL,
    version text NOT NULL,
    layer text NOT NULL CHECK (layer IN ('trust', 'scaffolding')),
    purpose text NOT NULL,
    metric jsonb NOT NULL CHECK (jsonb_typeof(metric) = 'object'),
    retirement_condition text,
    enabled boolean NOT NULL DEFAULT false,
    last_ablation jsonb,
    PRIMARY KEY (name, version),
    CHECK (layer <> 'scaffolding' OR nullif(btrim(retirement_condition), '') IS NOT NULL)
);

CREATE TABLE verifier_reports (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id uuid REFERENCES jobs(id),
    kind text NOT NULL CHECK (kind IN ('verifier_vulnerability', 'workflow_blocked')),
    report_uri text NOT NULL,
    report_sha256 sha256_hex NOT NULL,
    submitted_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE FUNCTION reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append only', TG_TABLE_NAME USING ERRCODE = '23514';
END
$$;
CREATE TRIGGER definitions_append_only BEFORE UPDATE OR DELETE ON definitions
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();
CREATE TRIGGER attempts_append_only BEFORE UPDATE OR DELETE ON attempts
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();
CREATE TRIGGER evidence_append_only BEFORE UPDATE OR DELETE ON evidence
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();
CREATE TRIGGER decisions_append_only BEFORE UPDATE OR DELETE ON decisions
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();

-- Every contract child write advances the parent's revision. This serializes
-- writes with freeze and forces stale repeatable-read writers to retry.
CREATE FUNCTION touch_draft_parent() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE parent_id uuid;
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.claim_id <> OLD.claim_id THEN
        RAISE EXCEPTION 'claim_id cannot change' USING ERRCODE = '23514';
    END IF;
    parent_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.claim_id ELSE NEW.claim_id END;
    UPDATE claims SET revision = revision + 1 WHERE id = parent_id AND status = 'draft';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'claim contract is frozen or missing' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER hypotheses_draft_only BEFORE INSERT OR UPDATE OR DELETE ON claim_hypotheses
    FOR EACH ROW EXECUTE FUNCTION touch_draft_parent();
CREATE TRIGGER conclusion_draft_only BEFORE INSERT OR UPDATE OR DELETE ON claim_conclusion
    FOR EACH ROW EXECUTE FUNCTION touch_draft_parent();
CREATE TRIGGER definition_links_draft_only BEFORE INSERT OR UPDATE OR DELETE ON claim_definitions
    FOR EACH ROW EXECUTE FUNCTION touch_draft_parent();

CREATE FUNCTION guard_claim() RETURNS trigger LANGUAGE plpgsql
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
        (OLD.status IN ('proved', 'refuted', 'superseded') AND NEW.status IN ('superseded', 'archived'))
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
CREATE TRIGGER check_claim BEFORE INSERT OR UPDATE OR DELETE ON claims
    FOR EACH ROW EXECUTE FUNCTION guard_claim();

CREATE FUNCTION guard_attempt() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE owned jobs%ROWTYPE;
BEGIN
    SELECT * INTO owned FROM jobs WHERE id = NEW.job_id FOR UPDATE;
    IF NOT FOUND OR owned.kind <> NEW.role OR owned.status <> 'running'
        OR owned.lease_generation <> NEW.lease_generation OR owned.lease_until <= clock_timestamp() THEN
        RAISE EXCEPTION 'missing, expired, or superseded job lease' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER check_attempt BEFORE INSERT ON attempts FOR EACH ROW EXECUTE FUNCTION guard_attempt();

CREATE FUNCTION guard_audit() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, rm, pg_temp AS $$
DECLARE author attempts%ROWTYPE;
DECLARE auditor attempts%ROWTYPE;
BEGIN
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
CREATE TRIGGER check_audit BEFORE INSERT ON evidence FOR EACH ROW EXECUTE FUNCTION guard_audit();

-- Direct access starts closed. The Step 0 interface document specifies the
-- service roles and transactions for the Alembic implementation.
REVOKE ALL ON SCHEMA rm FROM PUBLIC;
REVOKE EXECUTE ON ALL ROUTINES IN SCHEMA rm FROM PUBLIC;
