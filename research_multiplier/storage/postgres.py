"""Role-scoped connections and transactions for the production workflow."""

from contextlib import ExitStack
import os

from research_multiplier.storage import paths
from research_multiplier.policy.contracts import validate


_ROLES = ("human", "scheduler", "agent", "verifier")
_PROOF_ROLES = ("scheduler", "agent", "verifier")


class DatabaseOperationError(ValueError):
    def __init__(self, role, state, detail=None, *, retryable=False):
        self.role = role
        self.state = state
        self.detail = detail
        self.retryable = retryable
        super().__init__(f"{role} database operation failed [{state or 'connection'}]"
                         + (f": {detail}" if state and detail else ""))


def configured_worker_roles(*required):
    configured = (role for role in _PROOF_ROLES if os.environ.get(f"RM_{role.upper()}_DSN"))
    return tuple(dict.fromkeys((*required, *configured)))


def bind_workspace(workspace):
    """Attach the selected workspace to its verified database on explicit request."""
    with Store(allow_unbound=True) as store:
        store.verify_runtime_roles(roles=configured_worker_roles("human"))
        workspace_id = paths.ensure_workspace_id(workspace)

        def bind():
            identifier = store.query("human", "SELECT rm.bind_research_workspace(%s,%s) AS id",
                                     workspace_id, store.database)[0]["id"]
            # Publish the local intent before committing. A competing binding to a
            # different DB aborts here, and a stopped process can repeat this bind.
            paths.bind_database(workspace, identifier)
            return identifier

        return store.transaction("human", bind)


class Store:
    def __init__(self, *, allow_unbound=False):
        workspace = paths.active_workspace()
        self.strict = paths.strict_workspace() or workspace is not None
        self.database = paths.database_id(workspace)
        self.workspace_id = paths.workspace_id(workspace) if workspace is not None else None
        self.allow_unbound = allow_unbound
        if (self.strict or allow_unbound) and workspace is None:
            raise ValueError("Select an initialized research workspace before using PostgreSQL.")
        if self.strict and not allow_unbound and (self.database is None or self.workspace_id is None):
            raise ValueError("Bind this research workspace using 'research bind-workspace' before using PostgreSQL.")
        try:
            import psycopg
            from psycopg.rows import dict_row
            from psycopg.types.json import Jsonb
        except ImportError as error:
            raise ValueError("Install PostgreSQL dependencies with pip install '.[postgres]'.") from error
        self.connect = psycopg.connect
        self.database_error = psycopg.Error
        self.operational_error = psycopg.OperationalError
        self.row_factory = dict_row
        self.json = Jsonb
        self.stack = ExitStack()
        self.connections = {}
        self.verified_roles = set()

    def __enter__(self):
        return self

    def __exit__(self, *_exception):
        self.stack.close()

    def _connection(self, role):
        if role not in self.connections:
            setting = f"RM_{role.upper()}_DSN"
            dsn = os.environ.get(setting)
            if not dsn:
                raise ValueError(f"Set {setting} for the {role} service.")
            connection = self.connect(dsn, autocommit=True, row_factory=self.row_factory,
                                      connect_timeout=5, options="-c statement_timeout=10000")
            self.stack.callback(connection.close)
            binding = connection.execute("SELECT * FROM rm.current_workspace_binding()").fetchone()
            if binding is None:
                raise ValueError(f"The {role} database is missing its research identity.")
            if self.database is not None and binding["database_id"] != self.database:
                raise ValueError(f"The {role} login points to a different research database.")
            if (self.strict or self.workspace_id is not None) and (
                binding["workspace_id"] is not None and binding["workspace_id"] != self.workspace_id
            ):
                raise ValueError(f"The {role} database is bound to a different research workspace.")
            if self.strict and not self.allow_unbound and binding["workspace_id"] is None:
                raise ValueError("Bind this research workspace using 'research bind-workspace' before using PostgreSQL.")
            if self.strict:
                self._verify_role(role, connection)
            self.database = binding["database_id"]
            self.connections[role] = connection
        return self.connections[role]

    def _call(self, role, action):
        try:
            return action(self._connection(role))
        except self.database_error as error:
            # Connection failures can include DSNs in driver messages.
            state = getattr(error, "sqlstate", None)
            detail = getattr(getattr(error, "diag", None), "message_primary", None)
            transient = (state is None and isinstance(error, self.operational_error)
                         or bool(state and state.startswith("08"))
                         or state in {"53300", "57P01", "57P02", "57P03"})
            raise DatabaseOperationError(role, state, detail, retryable=transient) from None

    def query(self, role, statement, *values):
        return self._call(role, lambda conn: conn.execute(statement, values).fetchall())

    def transaction(self, role, action):
        """Run an action using this Store's connection for the selected role."""
        def run(connection):
            with connection.transaction():
                return action()

        return self._call(role, run)

    def _verify_role(self, role, connection):
        if role in self.verified_roles:
            return
        identity = connection.execute("""
            SELECT r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication OR r.rolbypassrls AS privileged,
                   ARRAY(SELECT g.rolname::text FROM pg_roles g
                         WHERE g.rolname IN ('rm_human', 'rm_scheduler', 'rm_agent', 'rm_verifier')
                           AND pg_has_role(current_user, g.oid, 'MEMBER')) AS memberships,
                   pg_has_role(current_user, %s, 'USAGE') AS inherits_role
            FROM pg_roles r WHERE r.rolname = current_user
        """, (f"rm_{role}",)).fetchone()
        if not identity["inherits_role"] or identity["privileged"] or identity["memberships"] != [f"rm_{role}"]:
            raise ValueError(f"The {role} DSN must use only the rm_{role} capability group.")
        self.verified_roles.add(role)

    def verify_runtime_roles(self, *, roles=_ROLES):
        for role in roles:
            self._call(role, lambda conn: self._verify_role(role, conn))

    def freeze(self, claim, actor_ref):
        validate("claim", "ClaimContract", claim)

        def freeze_in_transaction(conn):
            with conn.transaction():
                claim_id = conn.execute("SELECT rm.create_claim(%s) AS id", (self.json(claim),)).fetchone()["id"]
                conn.execute("SELECT rm.freeze_claim(%s, %s, %s)", (
                    claim_id, actor_ref, "Freeze public v0a statement before model calls",
                ))
        self._call("human", freeze_in_transaction)

    def frozen(self, claim_id):
        rows = self.query("verifier", """
            SELECT c.contract_sha256, c.contract_bytes, f.status, c.decisive_evidence_id
            FROM rm.claims c JOIN rm.claim_foundation_status f ON f.id = c.id
            WHERE c.id = %s AND c.frozen_at IS NOT NULL
        """, claim_id)
        return rows[0] if rows else None

    def enqueue(self, request, operation_key):
        validate("task", "JobRequest", request)
        subject = request["subject"]
        return self.query("scheduler", "SELECT * FROM rm.enqueue_stage(%s,%s,%s,%s,%s,%s)",
                          subject["claim_id"], subject["contract_sha256"], request["kind"],
                          self.json(request["payload"]), self.json(request["budget"]), operation_key)[0]

    def claim_job(self, kind, owner, seconds, job_id):
        return self.query("scheduler", "SELECT * FROM rm.claim_job(%s,%s,%s,%s)", kind, owner, seconds, job_id)

    def job_state(self, job_id):
        states = self.query("scheduler", """
            SELECT status, lease_generation, max_attempts FROM rm.jobs WHERE id = %s
        """, job_id)
        if not states:
            raise ValueError("The scheduled job no longer exists.")
        return states[0]

    def requeue_job(self, job_id):
        self.query("scheduler", "SELECT rm.requeue_job(%s)", job_id)

    def record_job_reuse(self, job_id):
        self.query("scheduler", "SELECT rm.record_job_reuse(%s)", job_id)

    def expire_exhausted_jobs(self):
        self.query("scheduler", "SELECT rm.expire_exhausted_jobs()")

    def proof_strategy(self, claim_id):
        rows = self.query("scheduler", """
            SELECT payload ->> 'strategy' AS strategy FROM rm.jobs
            WHERE claim_id = %s AND kind = 'prove' AND NOT (payload ? 'lean_author') ORDER BY enqueued_at, id LIMIT 1
        """, claim_id)
        return (rows[0]["strategy"] or "model") if rows else None

    def latest_attempt(self, job_id):
        rows = self.query("verifier", """
            SELECT * FROM rm.attempts WHERE job_id = %s
            ORDER BY lease_generation DESC LIMIT 1
        """, job_id)
        return rows[0] if rows else None

    def submit(self, kind, job_id, owner, generation, body, *, metrics=None):
        validate("task", "AttemptReturn", body)
        role = "verifier" if kind == "verify" else "agent"
        if metrics is not None:
            return self.query(role, "SELECT * FROM rm.submit_measured_attempt(%s,%s,%s,%s,%s)",
                              job_id, owner, generation, self.json(body), self.json(metrics))[0]
        return self.query(role, "SELECT * FROM rm.submit_attempt(%s,%s,%s,%s)",
                          job_id, owner, generation, self.json(body))[0]

    def issue_exact(self, body):
        validate("verification", "EvidenceReceipt", body)
        return self.query("verifier", "SELECT rm.issue_evidence(%s) AS id", self.json(body))[0]["id"]

    def review(self, evidence_id, action, actor_ref, reason, *, explanation=None, lesson=None, minutes=None):
        if any(value is not None for value in (explanation, lesson, minutes)):
            return self.query("human", "SELECT rm.review_with_notes(%s,%s,%s,%s,%s,%s,%s) AS id",
                              evidence_id, action, actor_ref, reason, explanation, lesson, minutes)[0]["id"]
        return self.query("human", "SELECT rm.review_evidence(%s,%s,%s,%s) AS id",
                          evidence_id, action, actor_ref, reason)[0]["id"]

    def promote(self, evidence_id, reason):
        return self.query("verifier", "SELECT rm.promote_claim(%s,%s) AS id",
                          evidence_id, reason)[0]["id"]
