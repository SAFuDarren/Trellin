"""Serve a project-bound proof graph on the IPv4 loopback interface."""

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
import logging
import os
import secrets
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4
import webbrowser

from research_multiplier.cli._output import json_default
from research_multiplier.models import connections
from research_multiplier.proof.lean_watch import LakeWatch
from research_multiplier.storage import paths
from research_multiplier.storage.managed_workspaces import LibraryError, uuid_text
from research_multiplier.storage.postgres import DatabaseOperationError, Store
from research_multiplier.workbench.runtime_updates import RuntimeUpdates
from research_multiplier.workflows import (claim_references, doctor, proof_graph, release_updates,
    research_progress, workbench_activity, workbench_agent_work, workbench_agents, workbench_automation,
    workbench_clarification, workbench_conversations, workbench_goal_review, workbench_guidance, workbench_intake,
    workbench_lemma_plans, workbench_library, workbench_premises, workbench_project_canvas, workbench_proof_drafts,
    workbench_proof_tasks, workbench_research_issues, workbench_review, workbench_sections, workbench_theorems,
    workspace_events)


# The interface is built from web/ into the package (docs/engineering.md); only its exact files are served.
_APP_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2", ".json": "application/json; charset=utf-8",
}
_POLICY = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
           "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
_LOG = logging.getLogger(__name__)
_ACCESS_LOG_LOCK = threading.Lock()
_MAX_GUIDANCE_BYTES = 16_384
_MAX_CLARIFICATION_BYTES = 64_000
_MAX_PROJECT_BYTES = 240_000
_DOCTOR_SECONDS = 120
_AGENT_WORK_RETRY_DELAYS = (0.25, 0.5, 1.0)
_SCHEMA_SQLSTATES = {"42883", "42P01", "42703"}
# Each workspace-wide page takes no parameters.
_SECTIONS = {
    "/api/inbox": workbench_sections.inbox,
    "/api/decision-records": workbench_sections.decision_records,
    "/api/library": workbench_sections.library,
    "/api/team": workbench_sections.team,
    "/api/progress": workbench_sections.progress,
    "/api/threads": workbench_sections.threads,
    "/api/project-intake": workbench_intake.recent,
    "/api/theorems": workbench_theorems.recent,
}
_PROJECT_WRITES = {
    "/api/project-intake": workbench_intake.draft,
    "/api/project-intake/confirm": workbench_intake.confirm,
    "/api/theorems": workbench_theorems.add,
    "/api/theorems/confirm": workbench_theorems.confirm,
}
_CONNECTION_WRITES = {
    "/api/model-connections/add": "add",
    "/api/model-connections/add-native": "add_native",
    "/api/model-connections/remove": "remove",
    "/api/model-connections/test": "test",
}
_TEAM_AGENTS_ROUTE = "/api/team-agents"
_TEAM_AGENTS_SAVE = "/api/team-agents/save"
_TEAM_AGENTS_ERRORS = {
    "team_agents.invalid": HTTPStatus.BAD_REQUEST,
    "team_agents.connection_unavailable": HTTPStatus.CONFLICT,
    "team_agents.conflict": HTTPStatus.CONFLICT,
    "team_agents.unavailable": HTTPStatus.SERVICE_UNAVAILABLE,
    "team_agents.limit": HTTPStatus.CONFLICT,
}
_AGENT_WORK_ROUTE = "/api/agent-work"
_AGENT_WORK_ERRORS = {
    "agentWork.invalid": HTTPStatus.BAD_REQUEST,
    "agentWork.identity": HTTPStatus.FORBIDDEN,
    "agentWork.missing": HTTPStatus.NOT_FOUND,
    "agentWork.runtimeUnavailable": HTTPStatus.SERVICE_UNAVAILABLE,
    "agentWork.unavailable": HTTPStatus.SERVICE_UNAVAILABLE,
}
_PROJECT_CANVAS_ROUTE = "/api/project-canvas"
_PROJECT_CANVAS_CLAIMS = _PROJECT_CANVAS_ROUTE + "/claims"
_PROJECT_CANVAS_ERRORS = {
    "project_canvas.invalid": HTTPStatus.BAD_REQUEST,
    "project_canvas.missing": HTTPStatus.CONFLICT,
    "project_canvas.unavailable": HTTPStatus.SERVICE_UNAVAILABLE,
}
_INTAKE_PUBLIC = ("/api/project-intake/public-draft",)
# #355: a project's standing permission to send import material to a connected model (D4).
_INTAKE_GRANTS = "/api/project-intake/grants"
_INTAKE_GRANT_WRITES = {"/api/project-intake/grant": "grant", "/api/project-intake/revoke": "revoke"}
_PROOF_WRITES = {"/api/proof-task/start", "/api/proof-task/stop"}
# #94: create, approve and stop automation rules from the workbench.
_AUTOMATION_ROUTE = "/api/automation"
_AUTOMATION_WRITES = {_AUTOMATION_ROUTE + "/propose", _AUTOMATION_ROUTE + "/approve", _AUTOMATION_ROUTE + "/stop"}
_PROOF_DRAFT_ROUTE = "/api/proof-drafts"
_PROOF_DRAFT_DISCARD = "/api/proof-drafts/discard"
_PROOF_DRAFT_ERRORS = {
    "proof_draft_invalid": HTTPStatus.BAD_REQUEST,
    "proof_draft_missing": HTTPStatus.NOT_FOUND,
    "proof_draft_conflict": HTTPStatus.CONFLICT,
    "proof_draft_unavailable": HTTPStatus.SERVICE_UNAVAILABLE,
}
_LEMMA_PLAN_ROUTE = "/api/lemma-plans"
_LEMMA_PLAN_ERRORS = {
    "lemmaPlan.invalid": HTTPStatus.BAD_REQUEST,
    "lemmaPlan.identity": HTTPStatus.FORBIDDEN,
    "lemmaPlan.stale": HTTPStatus.CONFLICT,
    "lemmaPlan.busy": HTTPStatus.CONFLICT,
    "lemmaPlan.checkFailed": HTTPStatus.UNPROCESSABLE_ENTITY,
}
_PREMISE_WRITES = {
    "/api/premises/propose": workbench_premises.propose,
    "/api/premises/decide": workbench_premises.decide,
}
_PREMISE_ERRORS = {
    "premise.invalid": HTTPStatus.BAD_REQUEST,
    "premise.identity": HTTPStatus.FORBIDDEN,
    "premise.unavailable": HTTPStatus.SERVICE_UNAVAILABLE,
    "premise.stale": HTTPStatus.CONFLICT,
    "premise.duplicate": HTTPStatus.CONFLICT,
    "premise.conflicts": HTTPStatus.CONFLICT,
}
_GOAL_REVIEW_ROUTE = "/api/goal-review"
_GOAL_SELECT_ROUTE = "/api/goal-review/select"
_GOAL_RELATED_ROUTE = "/api/goal-review/related"
_GOAL_REVIEW_ERRORS = {
    "goalReview.invalid": HTTPStatus.BAD_REQUEST,
    "goalReview.identity": HTTPStatus.FORBIDDEN,
    "goalReview.missing": HTTPStatus.NOT_FOUND,
    "goalReview.stale": HTTPStatus.CONFLICT,
    "goalReview.unavailable": HTTPStatus.SERVICE_UNAVAILABLE,
}
_INTAKE_REVIEW_ROUTE = "/api/intake-review"
_INTAKE_REVIEW_CONFIRM_ROUTE = "/api/intake-review/confirm"
_INTAKE_REVIEW_ERRORS = {
    "intakeReview.invalid": HTTPStatus.BAD_REQUEST,
    "intakeReview.identity": HTTPStatus.FORBIDDEN,
    "intakeReview.missing": HTTPStatus.NOT_FOUND,
    "intakeReview.stale": HTTPStatus.CONFLICT,
    "intakeReview.unavailable": HTTPStatus.SERVICE_UNAVAILABLE,
}
_CONVERSATION_CREATE = "/api/conversations/create"
_CONVERSATION_ARCHIVE = "/api/conversations/archive"
_CONVERSATION_WRITES = {_CONVERSATION_CREATE, _CONVERSATION_ARCHIVE}
_CONVERSATION_INFERENCE = {"/api/conversations/preview", "/api/conversations/send"}
_LIBRARY_WRITES = {"/api/library/preview", "/api/library/revise"}
_LIBRARY_ERRORS = {
    "libraryEdit.invalid": HTTPStatus.BAD_REQUEST, "libraryEdit.missing": HTTPStatus.NOT_FOUND,
    "libraryEdit.stale": HTTPStatus.CONFLICT, "libraryEdit.identity": HTTPStatus.FORBIDDEN,
    "libraryEdit.unavailable": HTTPStatus.CONFLICT,
}
_CLARIFICATION_WRITES = {
    "answer": workbench_clarification.answer,
    "prepare": workbench_clarification.prepare,
    "confirm": workbench_clarification.confirm,
}
_V1_ERRORS = {
    "request.invalid": (HTTPStatus.BAD_REQUEST, "Supply a valid request and idempotency key."),
    "request.unauthenticated": (HTTPStatus.UNAUTHORIZED, "Open the workbench from its launcher."),
    "request.forbidden": (HTTPStatus.FORBIDDEN, "Submit this request from the local workbench."),
    "review.identity_required": (HTTPStatus.FORBIDDEN, "Bind the workspace to its database to resolve a local user."),
    "review.not_found": (HTTPStatus.NOT_FOUND, "The requested review record is unavailable in this workspace."),
    "review.not_pending": (HTTPStatus.CONFLICT, "The evidence is no longer eligible for this review."),
    "clarification.invalid": (HTTPStatus.BAD_REQUEST, "Supply the requested answers, scope or review details."),
    "clarification.not_found": (HTTPStatus.NOT_FOUND, "The clarification is unavailable in this workspace."),
    "clarification.identity_required": (HTTPStatus.FORBIDDEN, "Bind the workspace to resolve a local researcher."),
    "clarification.unavailable": (HTTPStatus.CONFLICT, "The clarification needs an upgraded database and its archive."),
    "clarification.stale": (HTTPStatus.CONFLICT, "The clarification changed. Reload it to review the current state."),
    "clarification.scope": (HTTPStatus.FORBIDDEN, "Confirm the required scope for this model request."),
    "clarification.busy": (HTTPStatus.CONFLICT, "A process is already preparing the clarification."),
    "clarification.prepare_failed": (HTTPStatus.CONFLICT, "Preparation failed. The recorded answers are available to retry."),
    "idempotency.conflict": (HTTPStatus.CONFLICT, "This request key was already used for a different request."),
    "database.unavailable": (HTTPStatus.SERVICE_UNAVAILABLE, "The selected research database is unavailable."),
    "request.failed": (HTTPStatus.INTERNAL_SERVER_ERROR, "Could not complete the workbench request."),
    "events.not_found": (HTTPStatus.NOT_FOUND, "The requested workspace event stream is unavailable."),
    "events.invalid_cursor": (HTTPStatus.BAD_REQUEST, "The event cursor is invalid for this workspace."),
    "events.unavailable": (HTTPStatus.CONFLICT, "Upgrade the project database to use workspace events."),
    "events.identity_required": (HTTPStatus.FORBIDDEN, "Bind the workspace to resolve a local user."),
    "claims.invalid_query": (HTTPStatus.BAD_REQUEST, "The recent claims list does not take parameters."),
    "claims.not_found": (HTTPStatus.NOT_FOUND, "The requested workspace claims are unavailable."),
    "claim_activity.invalid_query": (HTTPStatus.BAD_REQUEST, "Claim activity does not take query parameters."),
    "claim_activity.not_found": (HTTPStatus.NOT_FOUND, "The requested claim activity is unavailable in this workspace."),
    "claim_graph.invalid_query": (HTTPStatus.BAD_REQUEST, "The claim graph does not take query parameters."),
    "claim_graph.not_found": (HTTPStatus.NOT_FOUND, "The requested claim graph is unavailable in this workspace."),
    "research_issue.invalid": (HTTPStatus.BAD_REQUEST, "Check the research direction and any selected file references."),
    "research_issue.invalid_query": (HTTPStatus.BAD_REQUEST, "Research Work reads do not take query parameters."),
    "research_issue.input_changed": (HTTPStatus.CONFLICT, "A selected input is missing or has a different version."),
    "research_issue.input_unavailable": (HTTPStatus.CONFLICT, "The selected source input cannot be verified."),
    "research_issue.storage_unavailable": (HTTPStatus.SERVICE_UNAVAILABLE, "Use Back up and upgrade for this project to enable research Work."),
    "research_issue.identity_required": (HTTPStatus.FORBIDDEN, "Bind the workspace to resolve a local researcher."),
    "research_issue.not_found": (HTTPStatus.NOT_FOUND, "The workspace or research source is unavailable."),
    "research_issue.stale": (HTTPStatus.CONFLICT, "The proof note changed. Reopen its current draft."),
    "research_issue.source_unavailable": (HTTPStatus.SERVICE_UNAVAILABLE, "Proof note storage is unavailable."),
    "agent_work.invalid_query": (HTTPStatus.BAD_REQUEST, "Agent work reads do not take query parameters."),
    "agent_work.not_found": (HTTPStatus.NOT_FOUND, "The requested Agent work is unavailable in this workspace."),
    "agent_work.identity_required": (HTTPStatus.FORBIDDEN, "Bind the workspace to resolve a local researcher."),
    "agent_work.upgrade_required": (HTTPStatus.CONFLICT, "Agent work requires an upgraded workspace database."),
    "agent_work.delivery_unavailable": (HTTPStatus.CONFLICT, "The recorded Agent delivery is unavailable."),
}


class _BadRequest(ValueError):
    pass


class _MissingAssets(ValueError):
    pass


class _MissingSchema(ValueError):
    pass


class _MissingWorkspace(ValueError):
    pass


def _uuid_parameters(query, *fields):
    try:
        values = parse_qs(query, keep_blank_values=True, strict_parsing=True, max_num_fields=len(fields))
        if values.keys() != set(fields) or any(len(value) != 1 for value in values.values()):
            raise ValueError
        return tuple(UUID(values[field][0]) for field in fields)
    except (ValueError, KeyError):
        raise _BadRequest("Supply one valid UUID for each requested reference.") from None


def _reference_parameter(query):
    try:
        values = parse_qs(query, keep_blank_values=True, strict_parsing=True, max_num_fields=1)
        if values.keys() != {"code"} or len(values["code"]) != 1:
            raise ValueError
        return values["code"][0]
    except ValueError:
        raise _BadRequest("claim_code.invalid") from None


def _claim_code_error(error):
    code = str(error)
    return {
        "claim_code.invalid": HTTPStatus.BAD_REQUEST,
        "claim_code.missing": HTTPStatus.NOT_FOUND,
    }.get(code, HTTPStatus.CONFLICT), code


def _app_assets():
    """Map the built interface to exact request paths; anything not built is simply absent."""
    app = files("research_multiplier.workbench").joinpath("app")
    if not app.is_dir():
        return {}
    found = {}
    index = app.joinpath("index.html")
    if index.is_file():
        found["/"] = (index.read_bytes(), _APP_TYPES[".html"])
    assets = app.joinpath("assets")
    if assets.is_dir():
        for entry in assets.iterdir():
            suffix = "." + entry.name.rsplit(".", 1)[-1] if "." in entry.name else ""
            if entry.is_file() and suffix in _APP_TYPES:
                found[f"/assets/{entry.name}"] = (entry.read_bytes(), _APP_TYPES[suffix])
    return found


class WorkbenchServer(ThreadingHTTPServer):
    def __init__(self, workspace, claim_id=None, port=0):
        self.workspace = workspace
        self.claim_id = claim_id
        self.session = secrets.token_urlsafe(32)
        self.assets = _app_assets()
        if "/" not in self.assets:
            raise _MissingAssets("The workbench interface is missing from this installation. Reinstall the package.")
        self._doctor = (0.0, None)
        self._doctor_lock = threading.Lock()
        self._intake_lock = threading.Lock()
        self.connections = connections.Registry()
        self.team_agents = workbench_agents.AgentProfiles(workspace)
        self.project_canvas = workbench_project_canvas.ProjectCanvas(workspace)
        self.public_intake = workbench_intake.PublicIntake(self.connections)
        self.discussions = workbench_conversations.Discussions(self.connections, self.team_agents)
        self.agent_work = workbench_agent_work.AgentWork(self.connections, self.team_agents)
        self._agent_work_lock = threading.Lock()
        self._agent_work_wakeup = threading.Event()
        self._agent_work_thread = None
        self._proof_task_lock = threading.Lock()
        self._lemma_plan_lock = threading.Lock()
        self.closing = threading.Event()
        self.source_observer = None
        self._running_policy = None
        self._failed_policy = None
        self.runtime_updates = RuntimeUpdates(self._local_proof_worker)
        self.release_updates = release_updates.ReleaseUpdates()
        super().__init__(("127.0.0.1", port), _Handler)
        self.host = f"127.0.0.1:{self.server_port}"
        self.origin = f"http://{self.host}"

    @property
    def launch_url(self):
        return f"{self.origin}/#session={self.session}"

    def server_close(self):
        self.closing.set()
        self._agent_work_wakeup.set()
        super().server_close()

    def read(self, action, *values):
        # Request threads do not inherit the CLI's workspace ContextVar.
        with paths.use_workspace(self.workspace, strict=True), Store() as store:
            return store.transaction("human", lambda: action(store, *values), read_only=True)

    def write(self, action, *values):
        with paths.use_workspace(self.workspace, strict=True), Store() as store:
            return store.transaction("human", lambda: action(store, *values))

    def _offer_source_inputs(self, reference_ids):
        if self.source_observer is None:
            raise workbench_research_issues.ResearchIssuePreviewError("research_issue.input_unavailable")
        try:
            return self.source_observer.verified_inputs(reference_ids)
        except LibraryError:
            raise workbench_research_issues.ResearchIssuePreviewError("research_issue.input_unavailable") from None

    def _research_work_snapshot(self, store):
        observations = self.source_observer.current_snapshot() if self.source_observer is not None else None
        records = workbench_research_issues.list_offers(store, observations)
        return {"items": records["issues"], "next_cursor": None}

    def verify_lemma_plan(self, document):
        if self.closing.is_set() or not self._lemma_plan_lock.acquire(blocking=False):
            raise workbench_lemma_plans.LemmaPlanError("lemmaPlan.busy")
        try:
            # The verifier records its own receipts after local Lean returns.
            with paths.use_workspace(self.workspace, strict=True), Store() as store:
                return workbench_lemma_plans.verify(store, document)
        finally:
            self._lemma_plan_lock.release()

    def prepare_clarification(self, workspace_id, identifier, document):
        # The existing workflow locks the request and commits only after the model returns.
        with paths.use_workspace(self.workspace, strict=True), Store() as store:
            return workbench_clarification.prepare(store, workspace_id, identifier, document)

    def write_project(self, action, document):
        if action not in (workbench_intake.draft, self.public_intake.draft):
            return self.write(action, document)
        if not self._intake_lock.acquire(blocking=False):
            raise workbench_intake.IntakeError("intake.busy")
        try:
            # Intake makes one durable database write after inference. A slow
            # local model need not hold a database transaction open.
            with paths.use_workspace(self.workspace, strict=True), Store() as store:
                return action(store, document)
        finally:
            self._intake_lock.release()

    def send_conversation(self, document):
        result, accepted = self.write(self.discussions.accept, document)
        if accepted:
            identifier = result["thread"]["id"]
            request = UUID(document["request_id"])
            try:
                threading.Thread(target=self._work_on_conversation, args=(identifier, request), daemon=True).start()
            except RuntimeError:
                self.write(workbench_conversations.interrupted, identifier, request)
                return self.read(workbench_conversations.thread, self.team_agents, identifier)
        return result

    def agent_work_tasks(self):
        result = self.read(workbench_agent_work.list_tasks)
        if result["available"] and any(task["status"] in {"queued", "running"} for task in result["tasks"]):
            self._wake_agent_work()
        return result

    def dispatch_agent_work(self, action, document):
        result = self.write(action, document)
        if result["task"]["status"] in {"queued", "running"}:
            self._wake_agent_work()
        return result

    def _wake_agent_work(self):
        if self.closing.is_set() or not os.environ.get("RM_SCHEDULER_DSN"):
            return
        with self._agent_work_lock:
            if self._agent_work_thread is None or not self._agent_work_thread.is_alive():
                self._agent_work_thread = threading.Thread(target=self._run_agent_work, daemon=True)
                try:
                    self._agent_work_thread.start()
                except RuntimeError:
                    self._agent_work_thread = None
                    return  # The queued record is available on the next open.
            self._agent_work_wakeup.set()

    def _run_agent_work(self):
        worker = uuid4()
        retries = 0
        while not self.closing.is_set():
            self._agent_work_wakeup.clear()
            try:
                with paths.use_workspace(self.workspace, strict=True), Store() as store:
                    worked = self.agent_work.work_one(store, worker)
            except (ValueError, OSError) as error:
                retryable = isinstance(error, OSError) or (
                    isinstance(error, DatabaseOperationError) and error.role == "scheduler" and error.retryable
                )
                if not retryable:
                    _LOG.warning("Workbench Agent work polling paused (%s).", type(error).__name__)
                    break
                if retries == len(_AGENT_WORK_RETRY_DELAYS):
                    _LOG.warning("Workbench Agent work polling paused after %d retries (%s).",
                                 retries, type(error).__name__)
                    break
                delay = _AGENT_WORK_RETRY_DELAYS[retries]
                retries += 1
                _LOG.info("Workbench Agent work polling retry %d in %.2fs (%s).",
                          retries, delay, type(error).__name__)
                if self.closing.wait(delay):
                    break
                # Poll the durable queue again; a claimed attempt is never replayed by this loop.
                continue
            retries = 0
            if worked:
                continue
            with self._agent_work_lock:
                if not self._agent_work_wakeup.is_set():
                    self._agent_work_thread = None
                    return
        with self._agent_work_lock:
            self._agent_work_thread = None

    def _work_on_conversation(self, thread_id, request_id):
        try:
            with paths.use_workspace(self.workspace, strict=True), Store() as store:
                self.discussions.run(store, thread_id, request_id)
        except Exception as error:
            _LOG.error("Workbench conversation worker stopped (%s).", type(error).__name__)
            # An ambiguous dispatch is never replayed. Recover the thread and
            # keep its submitted message even if the worker stopped.
            try:
                self.write(workbench_conversations.interrupted, thread_id, request_id)
            except (ValueError, OSError):
                pass  # The durable deadline exposes the interruption on re-open.

    def doctor(self, fresh=False):
        """Run the read-only installation check, reusing a recent report unless a fresh one is requested."""
        with self._doctor_lock:
            checked, report = self._doctor
            if fresh or report is None or time.monotonic() - checked > _DOCTOR_SECONDS:
                with paths.use_workspace(self.workspace, strict=True):
                    report = doctor.diagnose()
                self._doctor = (time.monotonic(), report)
            return report

    def add_guidance(self, claim_id, statement_id, request_id, node_id, text, audience, confirmed, draft_revision=None):
        return self.write(workbench_proof_drafts.queue_guidance, claim_id, statement_id, request_id,
                          node_id, text, audience, confirmed, draft_revision)

    def _local_proof_worker(self):
        with self._proof_task_lock:
            return self._running_policy, self._failed_policy

    def proof_task(self, claim_id):
        task = self.read(workbench_proof_tasks.snapshot, claim_id)
        policy = task["policy"]
        if policy and task["state"] in {"authorized", "waiting"}:
            with self._proof_task_lock:
                if str(policy["id"]) == self._running_policy:
                    return {**task, "state": "running", "can_start": False}
                if str(policy["id"]) == self._failed_policy:
                    return {**task, "state": "failed", "blocker": "proof_task.worker_failed"}
        return task

    def start_proof_task(self, document):
        with self._proof_task_lock:
            if self._running_policy is not None:
                raise workbench_proof_tasks.ProofTaskError("proof_task.busy")
            result = self.write(workbench_proof_tasks.start, document)
            if not self._start_worker(str(result["policy_id"]), "public_model" in document):
                raise workbench_proof_tasks.ProofTaskError("proof_task.worker_failed")
            return result

    def _start_worker(self, identifier, connected_model=False):
        """Work on one approved policy in the background. The caller holds the proof task lock."""
        self._running_policy = identifier
        self._failed_policy = None
        try:
            threading.Thread(target=self._work_on_proof, args=(identifier, connected_model), daemon=True).start()
        except RuntimeError:
            self._running_policy = None
            self._failed_policy = identifier
            return False
        return True

    def approve_automation(self, document):
        """Approve the reviewed rule, then give it a first run when the project's worker slot is free (#94)."""
        with self._proof_task_lock:
            result = self.write(workbench_automation.approve, document)
            if self._running_policy is not None:
                return {**result, "run": "waiting"}
            return {**result, "run": "started" if self._start_worker(result["policy_id"]) else "failed"}

    def _work_on_proof(self, identifier, connected_model):
        failed = True
        try:
            with paths.use_workspace(self.workspace, strict=True):
                lake_watch = LakeWatch()
                failed = False
                while not self.closing.is_set():
                    cycle = research_progress.advance_cycle("combined", 1, [identifier], 1, lake_watch=lake_watch,
                                                           public_model_policies=connected_model)
                    failed = bool(cycle["errors"])
                    if failed or not cycle["lean_runs"] or cycle["lean_runs"][0]["status"] != "search_continues":
                        break
        except Exception as error:
            failed = True
            _LOG.error("Workbench proof worker stopped (%s).", type(error).__name__)
        finally:
            with self._proof_task_lock:
                self._running_policy = None
                if failed:
                    self._failed_policy = identifier


def _activity(store, claim_id):
    return {
        **workbench_activity.snapshot(store, claim_id), **workbench_guidance.activity(store, claim_id),
        "proof_draft_available": workbench_proof_drafts.installed(store),
    }


def _workspace_snapshot(store, workspace_id, snapshot, *values):
    if store.workspace_id != workspace_id:
        raise _MissingWorkspace
    return snapshot(store, *values)


def _claims_snapshot(store, claim_id):
    index = proof_graph.claim_index(store)
    payload = {"items": index["claims"], "next_cursor": None, "claim_id": claim_id}
    if "claim_codes" in index:
        payload["claim_codes"] = index["claim_codes"]
    return payload


def _agent_work_snapshot(store):
    snapshot = workbench_agent_work.list_tasks(store)
    return {"items": snapshot["tasks"], "next_cursor": None, "available": snapshot["available"]}


class _Handler(BaseHTTPRequestHandler):
    server_version = "ResearchMultiplier/1.0"
    request_id = None

    def setup(self):
        self.request.settimeout(10)
        super().setup()

    def log_message(self, *_):
        pass  # Do not record project URLs, IDs or request headers.

    def _headers(self, status, content, length=None):
        self.send_response(status)
        self.send_header("Content-Type", content)
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", _POLICY)
        if self.request_id is not None:
            self.send_header("X-Request-Id", self.request_id)
        self.end_headers()

    def _send(self, status, content, body):
        self._headers(status, content, len(body))
        self.wfile.write(body)

    def _json(self, status, payload):
        body = json.dumps(payload, default=json_default, ensure_ascii=False).encode("utf-8")
        self._send(status, "application/json; charset=utf-8", body)

    def _error(self, status, message):
        self._json(status, {"error": message})

    def _trusted(self):
        if self.headers.get_all("Host") != [self.server.host]:
            return False
        origins = self.headers.get_all("Origin")
        return not origins or origins == [self.server.origin]

    def _authorized(self):
        tokens = self.headers.get_all("X-RM-Session", ())
        return (len(tokens) == 1 and tokens[0].isascii()
                and secrets.compare_digest(tokens[0], self.server.session))

    def _read_api(self, route):
        if route.path == "/api/source-observations":
            if route.query:
                raise _BadRequest("This endpoint does not take parameters.")
            observer = self.server.source_observer
            if observer is None:
                return {"source_id": None, "revision": None, "status": "inactive", "files": [], "generation": 0}
            return observer.snapshot()
        if route.path == "/api/release-updates":
            if route.query:
                raise _BadRequest("This endpoint does not take parameters.")
            return self.server.release_updates.snapshot()
        if route.path == "/api/model-connections":
            if route.query:
                raise _BadRequest("connection.invalid")
            return {"connections": self.server.connections.list()}
        if route.path == "/api/model-connections/native":
            if route.query:
                raise _BadRequest("connection.invalid")
            return {"providers": self.server.connections.native()}
        if route.path == _TEAM_AGENTS_ROUTE:
            if route.query:
                raise _BadRequest("team_agents.invalid")
            return {"agents": self.server.team_agents.list()}
        if route.path == _AGENT_WORK_ROUTE:
            if route.query:
                return self.server.read(workbench_agent_work.task_detail, *_uuid_parameters(route.query, "task"))
            return self.server.agent_work_tasks()
        if route.path == _PROJECT_CANVAS_ROUTE:
            if route.query:
                raise _BadRequest("project_canvas.invalid")
            return self.server.project_canvas.snapshot()
        if route.path == _PROJECT_CANVAS_CLAIMS:
            if route.query:
                raise _BadRequest("project_canvas.invalid")
            return self.server.read(proof_graph.canvas_claim_index)
        if route.path == "/api/project-intake/models":
            if route.query:
                raise _BadRequest("This endpoint does not take parameters.")
            return workbench_intake.models()
        if route.path == _INTAKE_GRANTS:
            if route.query:
                raise _BadRequest("This endpoint does not take parameters.")
            return self.server.read(self.server.public_intake.grants)
        if route.path == "/api/conversations":
            claim = _uuid_parameters(route.query, "claim_id")[0] if route.query else None
            return self.server.read(workbench_conversations.threads, self.server.team_agents, claim)
        if route.path == "/api/conversations/thread":
            return self.server.read(workbench_conversations.thread, self.server.team_agents,
                                    *_uuid_parameters(route.query, "thread_id"))
        if route.path == "/api/claims":
            if route.query:
                raise _BadRequest("This endpoint does not take parameters.")
            return {**self.server.read(proof_graph.claim_index), "claim_id": self.server.claim_id}
        if route.path == "/api/atlas":
            if route.query:
                raise _BadRequest("This endpoint does not take parameters.")
            return self.server.read(proof_graph.atlas)
        if route.path in _SECTIONS:
            if route.query:
                raise _BadRequest("This endpoint does not take parameters.")
            return self.server.read(_SECTIONS[route.path])
        if route.path == "/api/doctor":
            if route.query not in ("", "fresh=1"):
                raise _BadRequest("Use fresh=1 to repeat the check.")
            return self.server.doctor(fresh=route.query == "fresh=1")
        if route.path == "/api/graph":
            return self.server.read(workbench_premises.graph, *_uuid_parameters(route.query, "claim"))
        if route.path == "/api/library/item":
            return self.server.read(workbench_library.item, *_uuid_parameters(route.query, "version"))
        if route.path == "/api/impact":
            return self.server.read(proof_graph.impact, *_uuid_parameters(route.query, "claim", "basis"))
        if route.path == _GOAL_REVIEW_ROUTE:
            return self.server.read(workbench_goal_review.detail, *_uuid_parameters(route.query, "goal"))
        if route.path == _INTAKE_REVIEW_ROUTE:
            return self.server.read(workbench_intake.review_detail, *_uuid_parameters(route.query, "intake"))
        if route.path == _PROOF_DRAFT_ROUTE:
            try:
                parameters = parse_qs(route.query, keep_blank_values=True, strict_parsing=True, max_num_fields=2)
                if parameters.keys() != {"claim_id", "node_id"} or any(len(v) != 1 for v in parameters.values()):
                    raise ValueError
                claim_id = UUID(parameters["claim_id"][0])
            except ValueError:
                raise _BadRequest("proof_draft_invalid") from None
            return self.server.read(workbench_proof_drafts.read, claim_id, parameters["node_id"][0])
        if route.path == "/api/activity":
            return self.server.read(_activity, *_uuid_parameters(route.query, "claim"))
        if route.path == "/api/proof-task":
            return self.server.proof_task(*_uuid_parameters(route.query, "claim"))
        if route.path == _AUTOMATION_ROUTE:
            if route.query:
                raise _BadRequest("This endpoint does not take parameters.")
            return self.server.read(workbench_automation.templates)
        if route.path == "/api/claim-code":
            return self.server.read(claim_references.resolve, _reference_parameter(route.query))
        return None

    def do_GET(self):
        if self.path.startswith("/api/v1/"):
            self._get_v1()
            return
        try:
            if not self._trusted():
                self._error(HTTPStatus.FORBIDDEN, "Open the workbench using its local launch address.")
                return
            try:
                route = urlsplit(self.path)
            except ValueError as error:
                raise _BadRequest("Invalid request path.") from error
            if route.scheme or route.netloc or route.fragment:
                raise _BadRequest("Use a local request path.")
            if route.path in self.server.assets and not route.query:
                body, content_type = self.server.assets[route.path]
                self._send(HTTPStatus.OK, content_type, body)
                return
            if not route.path.startswith("/api/"):
                self._error(HTTPStatus.NOT_FOUND, "Page unavailable.")
                return
            if not self._authorized():
                self._error(HTTPStatus.FORBIDDEN, "Open the workbench using the session link from its launcher.")
                return
            result = self._read_api(route)
            if result is None:
                self._error(HTTPStatus.NOT_FOUND, "Research record unavailable.")
                return
            self._json(HTTPStatus.OK, result)
        except _BadRequest as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
        except workbench_proof_drafts.ProofDraftError as error:
            self._error(_PROOF_DRAFT_ERRORS[str(error)], str(error))
        except workbench_proof_tasks.ProofTaskError as error:
            self._error(HTTPStatus.NOT_FOUND, error.code)
        except connections.ConnectionError as error:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, error.code)
        except workbench_agents.AgentProfileError as error:
            self._error(_TEAM_AGENTS_ERRORS[str(error)], str(error))
        except workbench_agent_work.AgentWorkError as error:
            self._error(_AGENT_WORK_ERRORS.get(error.code, HTTPStatus.CONFLICT), error.code)
        except workbench_project_canvas.ProjectCanvasError as error:
            self._error(_PROJECT_CANVAS_ERRORS[str(error)], str(error))
        except workbench_conversations.ConversationError as error:
            self._conversation_error(error.code)
        except workbench_goal_review.GoalReviewError as error:
            self._error(_GOAL_REVIEW_ERRORS[str(error)], str(error))
        except workbench_intake.IntakeReviewError as error:
            self._error(_INTAKE_REVIEW_ERRORS[str(error)], str(error))
        except workbench_library.LibraryError as error:
            self._error(_LIBRARY_ERRORS[str(error)], str(error))
        except claim_references.ClaimCodeError as error:
            self._error(*_claim_code_error(error))
        except DatabaseOperationError as error:
            if self.path.startswith(_PROOF_DRAFT_ROUTE + "?"):
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "proof_draft_unavailable")
            elif self.path.startswith(_GOAL_REVIEW_ROUTE):
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "goalReview.unavailable")
            elif self.path.startswith(_INTAKE_REVIEW_ROUTE):
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "intakeReview.unavailable")
            elif self.path == _PROJECT_CANVAS_CLAIMS:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "project_canvas.unavailable")
            elif error.state in _SCHEMA_SQLSTATES:
                self._error(HTTPStatus.CONFLICT, "This project database needs the workbench schema. See the workbench guide.")
            else:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Could not read the selected research database.")
        except ValueError:
            self._error(HTTPStatus.NOT_FOUND, "Research record unavailable in this project.")
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        except Exception as error:
            _LOG.error("Workbench request failed (%s).", type(error).__name__)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "Could not load the workbench view.")

    def _get_v1(self):
        self.request_id = str(uuid4())
        read_path = self.path.split("?", 1)[0].split("#", 1)[0].split("/")
        if len(read_path) in (6, 7) and read_path[3] == "workspaces":
            workspace, resource, *references = read_path[4:]
            if resource == "claims" and not references:
                self._get_workspace_read(workspace, resource, _claims_snapshot, self.server.claim_id)
                return
            if resource == "research-issues" and not references:
                self._get_workspace_read(workspace, resource, self.server._research_work_snapshot)
                return
            if resource == "agent-work":
                if references:
                    self._get_workspace_read(workspace, resource, workbench_agent_work.task_detail,
                                             record_id=references[0], route_path="agent-work/{task_id}")
                else:
                    self._get_workspace_read(workspace, resource, _agent_work_snapshot)
                return
        if len(read_path) == 8 and read_path[3] == "workspaces" and read_path[5] == "claims":
            workspace, claim_id, view = read_path[4], read_path[6], read_path[7]
            snapshot = {"activity": _activity, "graph": workbench_premises.graph}.get(view)
            if snapshot is not None:
                self._get_workspace_read(workspace, f"claim-{view}", snapshot,
                                         record_id=claim_id, route_path=f"claims/{{claim_id}}/{view}")
                return
        if not self._trusted():
            self._v1_error("request.forbidden")
            return
        if not self._authorized():
            self._v1_error("request.unauthenticated")
            return
        path = self.path.split("/")
        if len(path) > 5 and path[3] == "workspaces" and path[5] == "clarifications":
            self._v1_clarification(path)
            return
        if len(path) != 6 or path[3] != "workspaces" or path[5] != "events":
            self._v1_error("events.not_found")
            return
        try:
            workspace_id = UUID(path[4])
        except ValueError:
            self._v1_error("events.not_found")
            return
        cursors = self.headers.get_all("Last-Event-ID", ())
        if len(cursors) > 1 or (cursors and not 1 <= len(cursors[0]) <= 19):
            self._v1_error("events.invalid_cursor")
            return
        after = None
        if cursors:
            cursor = cursors[0]
            if not cursor.isascii() or not cursor.isdecimal() or int(cursor) >= 2**63:
                self._v1_error("events.invalid_cursor")
                return
            after = int(cursor)
        streaming = False
        runtime_version = (
            self.server.runtime_updates.version
            if self.headers.get("X-RM-Workbench-Updates") == workspace_events.FULL_COVERAGE else None
        )
        try:
            with paths.use_workspace(self.server.workspace, strict=True), Store() as store:
                with workspace_events.subscribe(
                    store, workspace_id, after, closing=self.server.closing, runtime_version=runtime_version,
                ) as events:
                    self.close_connection = True
                    streaming = True
                    self._headers(HTTPStatus.OK, "text/event-stream; charset=utf-8")
                    self.wfile.write(b": connected\n\n")
                    self.wfile.flush()
                    for event in events:
                        if event is None:
                            frame = b": heartbeat\n\n"
                        elif event is workspace_events.RUNTIME_CHANGED:
                            frame = b": workbench-runtime-changed\n\n"
                        else:
                            data = json.dumps(event, default=json_default, ensure_ascii=False, separators=(",", ":"))
                            frame = f"id: {event['id']}\nevent: {event['type']}\ndata: {data}\n\n".encode("utf-8")
                        self.wfile.write(frame)
                        self.wfile.flush()
        except workspace_events.WorkspaceEventError as error:
            if not streaming:
                self._v1_error(str(error))
        except DatabaseOperationError:
            if not streaming:
                self._v1_error("database.unavailable")
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        except Exception as error:
            _LOG.error("Workbench event stream failed (%s).", type(error).__name__)
            if not streaming:
                self._v1_error("request.failed")

    def _workspace_read_reply(self, workspace, resource, snapshot, *values, record_id=None):
        if not self._trusted():
            return "request.forbidden", None
        if not self._authorized():
            return "request.unauthenticated", None
        prefix = "research_issue" if resource == "research-issues" else resource.replace("-", "_")
        try:
            workspace_id = UUID(workspace)
            if str(workspace_id) != workspace.lower():
                raise ValueError
            if record_id is not None:
                record = UUID(record_id)
                if str(record) != record_id.lower():
                    raise ValueError
                values = (record, *values)
        except ValueError:
            return f"{prefix}.not_found", None
        if "?" in self.path or "#" in self.path:
            return f"{prefix}.invalid_query", None
        try:
            return None, self.server.read(_workspace_snapshot, workspace_id, snapshot, *values)
        except (_MissingWorkspace, workbench_activity.ClaimActivityNotFound, proof_graph.ClaimGraphNotFound):
            return f"{prefix}.not_found", None
        except workbench_research_issues.ResearchIssuePreviewError as error:
            return str(error), None
        except workbench_agent_work.AgentWorkError as error:
            code = {
                "agentWork.identity": "agent_work.identity_required",
                "agentWork.missing": "agent_work.not_found",
                "agentWork.upgrade": "agent_work.upgrade_required",
                "agentWork.deliveryUnavailable": "agent_work.delivery_unavailable",
                "agentWork.unavailable": "database.unavailable",
            }.get(error.code, "request.failed")
            return code, None
        except ValueError:
            # Store configuration and driver errors can contain sensitive connection details.
            return "database.unavailable", None
        except Exception:
            return "request.failed", None

    def _get_workspace_read(self, workspace, resource, snapshot, *values, record_id=None, route_path=None):
        start = time.monotonic()
        code, payload = self._workspace_read_reply(workspace, resource, snapshot, *values, record_id=record_id)
        route = f"/api/v1/workspaces/{{workspace_id}}/{route_path or resource}"
        self._v1_logged_reply(route, start, code, payload)

    def _v1_logged_reply(self, route, start, code, payload, *, success=HTTPStatus.OK):
        status = _V1_ERRORS[code][0] if code else success
        entry = {"route": route, "status": status,
                 "request_id": self.request_id, "duration_ms": round((time.monotonic() - start) * 1000, 3)}
        with _ACCESS_LOG_LOCK:
            print(json.dumps(entry, separators=(",", ":")), file=sys.stderr)
        try:
            if code:
                self._v1_error(code)
            else:
                self._json(status, payload)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass

    def _body(self, limit=_MAX_GUIDANCE_BYTES):
        if (self.headers.get_content_type() != "application/json" or self.headers.get_all("Transfer-Encoding")
                or self.headers.get_all("Content-Encoding")):
            raise _BadRequest("Send a UTF-8 JSON message.")
        lengths = self.headers.get_all("Content-Length", ())
        try:
            length = int(lengths[0]) if len(lengths) == 1 and lengths[0].isascii() and lengths[0].isdigit() else 0
            if not 0 < length <= limit:
                raise ValueError
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, RecursionError):
            raise _BadRequest("Send one UTF-8 JSON message.") from None

    def _review_body(self):
        body = self._body()
        try:
            required = {"evidence_id", "action", "reason"}
            if not isinstance(body, dict) or not required <= body.keys() <= required | {"actor"}:
                raise ValueError
            actor = body["actor"].strip() if "actor" in body else None
            reason = body["reason"].strip()
            if (body["action"] not in ("approve", "return") or not 0 < len(reason) <= 2000
                    or (actor is not None and not 0 < len(actor) <= 120)):
                raise ValueError
            return UUID(body["evidence_id"]), body["action"], actor, reason
        except (ValueError, AttributeError, TypeError):
            raise _BadRequest("Supply the evidence, approve or return, and a reason.") from None

    def _v1_error(self, code):
        status, message = _V1_ERRORS[code]
        self._json(status, {"error": {"code": code, "message": message, "request_id": self.request_id}})

    def _v1_clarification(self, path):
        try:
            if len(path) != (7 if self.command == "GET" else 8):
                raise workbench_clarification.ClarificationError("clarification.not_found")
            try:
                workspace_id, identifier = UUID(path[4]), UUID(path[6])
            except ValueError:
                raise workbench_clarification.ClarificationError("clarification.not_found") from None
            if self.command == "GET":
                result = self.server.read(workbench_clarification.detail, workspace_id, identifier)
            else:
                action = _CLARIFICATION_WRITES.get(path[7])
                if action is None:
                    raise workbench_clarification.ClarificationError("clarification.not_found")
                document = self._body(_MAX_CLARIFICATION_BYTES)
                if action is workbench_clarification.prepare:
                    result = self.server.prepare_clarification(workspace_id, identifier, document)
                else:
                    result = self.server.write(action, workspace_id, identifier, document)
            self._json(HTTPStatus.OK, result)
        except _BadRequest:
            self._v1_error("clarification.invalid")
        except workbench_clarification.ClarificationError as error:
            self._v1_error(str(error))
        except DatabaseOperationError as error:
            code = {"22023": "clarification.invalid", "23514": "clarification.stale",
                    "P0002": "clarification.stale", "42501": "clarification.identity_required"}
            self._v1_error(code.get(error.state, "database.unavailable"))
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        except Exception as error:
            _LOG.error("Versioned workbench clarification failed (%s).", type(error).__name__)
            self._v1_error("request.failed")

    def _v1_post_error(self):
        if not self._trusted() or self.headers.get_all("Origin") != [self.server.origin]:
            return "request.forbidden"
        if not self._authorized():
            return "request.unauthenticated"
        return None

    def _research_issue_reply(self, workspace, *, offer=False):
        if code := self._v1_post_error():
            return code, None
        try:
            workspace_id = UUID(workspace)
            if str(workspace_id) != workspace:
                raise ValueError
        except ValueError:
            return "research_issue.not_found", None
        if "?" in self.path or "#" in self.path:
            return "research_issue.invalid", None
        try:
            document = self._body(workbench_research_issues.MAX_REQUEST_BYTES)
            if not offer:
                return None, self.server.read(_workspace_snapshot, workspace_id, workbench_research_issues.preview, document)
            keys = self.headers.get_all("Idempotency-Key", ())
            if len(keys) != 1 or uuid_text(keys[0]) != keys[0]:
                return "research_issue.invalid", None
            if not isinstance(document, dict) or document.keys() not in ({"direction"}, {"direction", "inputs"}):
                return "research_issue.invalid", None
            return None, self.server.write(
                _workspace_snapshot, workspace_id, workbench_research_issues.offer,
                {"offer_id": keys[0], **document}, self.server._offer_source_inputs,
            )
        except (_BadRequest, LibraryError):
            return "research_issue.invalid", None
        except _MissingWorkspace:
            return "research_issue.not_found", None
        except workbench_research_issues.ResearchIssuePreviewError as error:
            code = str(error)
            return ("idempotency.conflict" if code == "research_issue.conflict" else code), None
        except (DatabaseOperationError, ValueError):
            return "database.unavailable", None
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            raise
        except Exception:
            return "request.failed", None

    def _post_v1(self):
        self.request_id = str(uuid4())
        try:
            research = self.path.split("?", 1)[0].split("#", 1)[0].split("/")
            if len(research) in (6, 7) and research[3] == "workspaces" and research[5:] in (
                ["research-issues"], ["research-issues", "preview"],
            ):
                start = time.monotonic()
                offer = len(research) == 6
                route = "/api/v1/workspaces/{workspace_id}/research-issues" + ("" if offer else "/preview")
                code, payload = self._research_issue_reply(research[4], offer=offer)
                self._v1_logged_reply(route, start, code, payload, success=HTTPStatus.CREATED if offer else HTTPStatus.OK)
                return
            if code := self._v1_post_error():
                self._v1_error(code)
                return
            path = self.path.split("/")
            if len(path) > 5 and path[3] == "workspaces" and path[5] == "clarifications":
                self._v1_clarification(path)
                return
            if len(path) != 8 or path[3] != "workspaces" or path[5] != "evidence" or path[7] != "reviews":
                self._v1_error("review.not_found")
                return
            try:
                workspace_id, evidence_id = UUID(path[4]), UUID(path[6])
            except ValueError:
                self._v1_error("review.not_found")
                return
            keys = self.headers.get_all("Idempotency-Key", ())
            try:
                if len(keys) != 1:
                    raise ValueError
                idempotency_key = UUID(keys[0])
                if str(idempotency_key) != keys[0].lower():
                    raise ValueError
            except ValueError:
                self._v1_error("request.invalid")
                return
            result = self.server.write(workbench_review.authenticated, workspace_id, evidence_id,
                                       self._body(), idempotency_key)
            self._json(HTTPStatus.CREATED, result)
        except _BadRequest:
            self._v1_error("request.invalid")
        except workbench_review.ReviewError as error:
            self._v1_error(str(error))
        except DatabaseOperationError:
            self._v1_error("database.unavailable")
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        except Exception as error:
            _LOG.error("Versioned workbench review failed (%s).", type(error).__name__)
            self._v1_error("request.failed")

    def _guidance_body(self):
        body = self._body()
        try:
            required = {"claim_id", "statement_id", "request_id", "node_id", "text"}
            if not isinstance(body, dict) or not required <= body.keys() <= required | {"audience", "confirm_release", "draft_revision"}:
                raise ValueError
            return (UUID(body["claim_id"]), UUID(body["statement_id"]), UUID(body["request_id"]),
                    body["node_id"], body["text"], body.get("audience", "local"), body.get("confirm_release", False),
                    UUID(body["draft_revision"]) if "draft_revision" in body else None)
        except (ValueError, AttributeError, TypeError, RecursionError):
            raise _BadRequest("Supply one valid guidance message with a claim, a node and a request ID.") from None

    def _proof_draft_write(self):
        body = self._body()
        discard = self.path == _PROOF_DRAFT_DISCARD
        fields = {"claim_id", "node_id", "expected_revision"}
        if not discard:
            fields.add("text")
        try:
            if not isinstance(body, dict) or body.keys() != fields:
                raise ValueError
            revision = body["expected_revision"]
            values = (UUID(body["claim_id"]), body["node_id"], UUID(revision) if discard or revision is not None else None)
        except (ValueError, AttributeError, TypeError):
            raise _BadRequest("proof_draft_invalid") from None
        if discard:
            return self.server.write(workbench_proof_drafts.discard, *values)
        return self.server.write(workbench_proof_drafts.save, *values, body["text"])

    def _change_connection(self, method):
        document = self._body()
        registry = self.server.connections
        if method == "add":
            return registry.add(document)
        if method == "add_native":
            return registry.add_native(document)
        if not isinstance(document, dict) or document.keys() != {"id"}:
            raise _BadRequest("connection.invalid")
        if method == "test":
            return registry.test(document["id"])
        registry.remove(document["id"])
        return {"removed": True}

    def do_POST(self):
        if self.path.startswith("/api/v1/"):
            self._post_v1()
            return
        try:
            if (not self._trusted() or self.headers.get_all("Origin") != [self.server.origin]
                    or not self._authorized()):
                self._error(HTTPStatus.FORBIDDEN, "Submit guidance from the local workbench session.")
            elif self.path == "/api/guidance":
                self._json(HTTPStatus.OK, self.server.add_guidance(*self._guidance_body()))
            elif self.path in {_PROOF_DRAFT_ROUTE, _PROOF_DRAFT_DISCARD}:
                self._json(HTTPStatus.OK, self._proof_draft_write())
            elif self.path == _CONVERSATION_CREATE:
                self._json(HTTPStatus.OK, self.server.write(workbench_conversations.create,
                                                            self.server.team_agents, self._body()))
            elif self.path == _CONVERSATION_ARCHIVE:
                self._json(HTTPStatus.OK, self.server.write(workbench_conversations.archive, self._body()))
            elif self.path == "/api/conversations/preview":
                self._json(HTTPStatus.OK, self.server.read(self.server.discussions.preview, self._body(64_000)))
            elif self.path == "/api/conversations/send":
                self._json(HTTPStatus.OK, self.server.send_conversation(self._body(64_000)))
            elif self.path == _AGENT_WORK_ROUTE + "/preview":
                self._json(HTTPStatus.OK, self.server.read(
                    self.server.agent_work.preview, self._body(workbench_agent_work.MAX_REQUEST_BYTES),
                ))
            elif self.path == _AGENT_WORK_ROUTE + "/start":
                self._json(HTTPStatus.OK, self.server.dispatch_agent_work(
                    self.server.agent_work.start, self._body(workbench_agent_work.MAX_REQUEST_BYTES),
                ))
            elif self.path == _AGENT_WORK_ROUTE + "/retry-preview":
                self._json(HTTPStatus.OK, self.server.read(self.server.agent_work.preview_retry, self._body()))
            elif self.path == _AGENT_WORK_ROUTE + "/retry":
                self._json(HTTPStatus.OK, self.server.dispatch_agent_work(self.server.agent_work.retry, self._body()))
            elif self.path == _AGENT_WORK_ROUTE + "/review":
                self._json(HTTPStatus.OK, self.server.write(workbench_agent_work.record_review, self._body()))
            elif self.path == "/api/library/preview":
                self._json(HTTPStatus.OK, self.server.read(workbench_library.preview, self._body(_MAX_PROJECT_BYTES)))
            elif self.path == "/api/library/revise":
                self._json(HTTPStatus.OK, self.server.write(workbench_library.confirm, self._body(_MAX_PROJECT_BYTES)))
            elif self.path == "/api/review":
                self._json(HTTPStatus.OK, self.server.write(workbench_sections.review, *self._review_body()))
            elif self.path == "/api/proof-task/start":
                self._json(HTTPStatus.OK, self.server.start_proof_task(self._body()))
            elif self.path == "/api/proof-task/stop":
                self._json(HTTPStatus.OK, self.server.write(workbench_proof_tasks.stop, self._body()))
            elif self.path == _AUTOMATION_ROUTE + "/propose":
                self._json(HTTPStatus.OK, self.server.write(workbench_automation.propose, self._body()))
            elif self.path == _AUTOMATION_ROUTE + "/approve":
                self._json(HTTPStatus.OK, self.server.approve_automation(self._body()))
            elif self.path == _AUTOMATION_ROUTE + "/stop":
                self._json(HTTPStatus.OK, self.server.write(workbench_automation.stop, self._body()))
            elif self.path == _LEMMA_PLAN_ROUTE:
                document = self._body(workbench_lemma_plans.MAX_REQUEST_BYTES)
                self._json(HTTPStatus.OK, self.server.verify_lemma_plan(document))
            elif self.path in _PREMISE_WRITES:
                self._json(HTTPStatus.OK, self.server.write(_PREMISE_WRITES[self.path], self._body(_MAX_PROJECT_BYTES)))
            elif self.path == _GOAL_SELECT_ROUTE:
                self._json(HTTPStatus.OK, self.server.write(workbench_goal_review.select, self._body(12_000)))
            elif self.path == _GOAL_RELATED_ROUTE:
                self._json(HTTPStatus.OK, self.server.read(workbench_goal_review.related, self._body(6000)))
            elif self.path == _INTAKE_REVIEW_CONFIRM_ROUTE:
                self._json(HTTPStatus.OK, self.server.write(workbench_intake.review_confirm,
                                                            self._body(_MAX_PROJECT_BYTES)))
            elif self.path == "/api/claim-code":
                self._json(HTTPStatus.OK, self.server.write(claim_references.rename, self._body()))
            elif self.path == "/api/release-updates":
                document = self._body(512)
                try:
                    result = self.server.release_updates.apply(document)
                except release_updates.SettingsUnavailable as error:
                    self._error(HTTPStatus.SERVICE_UNAVAILABLE, str(error))
                    return
                except ValueError as error:
                    raise _BadRequest(str(error)) from None
                self._json(HTTPStatus.OK, result)
            elif self.path in _CONNECTION_WRITES:
                self._json(HTTPStatus.OK, self._change_connection(_CONNECTION_WRITES[self.path]))
            elif self.path == _TEAM_AGENTS_SAVE:
                document = self._body(workbench_agents.MAX_REQUEST_BYTES)
                self._json(HTTPStatus.OK, {"agent": self.server.team_agents.save(document, self.server.connections)})
            elif self.path == _PROJECT_CANVAS_ROUTE:
                document = self._body(workbench_project_canvas.MAX_REQUEST_BYTES)
                self._json(HTTPStatus.OK, self.server.read(workbench_project_canvas.apply,
                                                          self.server.project_canvas, document))
            elif self.path in _INTAKE_GRANT_WRITES:
                action = getattr(self.server.public_intake, _INTAKE_GRANT_WRITES[self.path])
                self._json(HTTPStatus.OK, self.server.read(action, self._body(_MAX_PROJECT_BYTES)))
            elif self.path == "/api/project-intake/public-draft":
                self._json(HTTPStatus.OK, self.server.write_project(self.server.public_intake.draft,
                                                                   self._body(_MAX_PROJECT_BYTES)))
            elif self.path in _PROJECT_WRITES:
                document = self._body(_MAX_PROJECT_BYTES)
                if not isinstance(document, dict):
                    raise _BadRequest("intake.invalid")
                self._json(HTTPStatus.OK, self.server.write_project(_PROJECT_WRITES[self.path], document))
            else:
                self._error(HTTPStatus.NOT_FOUND, "Page unavailable.")
        except _BadRequest as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
        except workbench_agent_work.AgentWorkError as error:
            self._error(_AGENT_WORK_ERRORS.get(error.code, HTTPStatus.CONFLICT), error.code)
        except workbench_review.ReviewError as error:
            if str(error) == "review.legacy_name_required":
                self._error(HTTPStatus.BAD_REQUEST, "Supply the evidence, approve or return, your name and a reason.")
            else:
                self._error(*_V1_ERRORS[str(error)])
        except workbench_guidance.GuidanceError as error:
            self._error(HTTPStatus.CONFLICT, str(error))
        except workbench_proof_drafts.ProofDraftError as error:
            self._error(_PROOF_DRAFT_ERRORS[str(error)], str(error))
        except workbench_proof_tasks.ProofTaskError as error:
            status = HTTPStatus.BAD_REQUEST if error.code == "proof_task.invalid" else HTTPStatus.CONFLICT
            self._error(status, error.code)
        except workbench_automation.AutomationError as error:
            invalid = error.code in {"automation.invalid", "automation.reason"}
            self._error(HTTPStatus.BAD_REQUEST if invalid else HTTPStatus.CONFLICT, error.code)
        except workbench_lemma_plans.LemmaPlanError as error:
            self._error(_LEMMA_PLAN_ERRORS[str(error)], str(error))
        except workbench_premises.PremiseError as error:
            self._error(_PREMISE_ERRORS[str(error)], str(error))
        except claim_references.ClaimCodeError as error:
            self._error(*_claim_code_error(error))
        except connections.ConnectionError as error:
            self._error(HTTPStatus.CONFLICT, error.code)
        except workbench_agents.AgentProfileError as error:
            self._error(_TEAM_AGENTS_ERRORS[str(error)], str(error))
        except workbench_project_canvas.ProjectCanvasError as error:
            self._error(_PROJECT_CANVAS_ERRORS[str(error)], str(error))
        except workbench_conversations.ConversationError as error:
            self._conversation_error(error.code)
        except workbench_intake.IntakeReviewError as error:
            self._error(_INTAKE_REVIEW_ERRORS[str(error)], str(error))
        except (workbench_intake.IntakeError, workbench_theorems.TheoremError) as error:
            self._error(HTTPStatus.CONFLICT, str(error))
        except workbench_goal_review.GoalReviewError as error:
            self._error(_GOAL_REVIEW_ERRORS[str(error)], str(error))
        except workbench_library.LibraryError as error:
            self._error(_LIBRARY_ERRORS[str(error)], str(error))
        except DatabaseOperationError as error:
            if self.path in _CONVERSATION_WRITES or self.path in _CONVERSATION_INFERENCE:
                code = {
                    "42501": "identity", "P0002": "missing", "55006": "busy",
                    "23514": "stale", "23505": "stale",
                }.get(error.state, "unavailable")
                self._conversation_error(f"conversation.{code}")
            elif self.path in {_PROOF_DRAFT_ROUTE, _PROOF_DRAFT_DISCARD}:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "proof_draft_unavailable")
            elif self.path in _PROOF_WRITES:
                status = HTTPStatus.SERVICE_UNAVAILABLE if error.retryable else HTTPStatus.CONFLICT
                self._error(status, "proof_task.unavailable" if error.retryable else "proof_task.stale")
            elif self.path in _AUTOMATION_WRITES:
                status = HTTPStatus.SERVICE_UNAVAILABLE if error.retryable else HTTPStatus.CONFLICT
                self._error(status, "automation.unavailable" if error.retryable else "automation.stale")
            elif self.path == _LEMMA_PLAN_ROUTE:
                changed = error.state in {"P0002", "23514"}
                self._error(HTTPStatus.CONFLICT if changed else HTTPStatus.SERVICE_UNAVAILABLE,
                            "lemmaPlan.stale" if changed else "lemmaPlan.unavailable")
            elif self.path in _PREMISE_WRITES:
                code = {"42501": "identity", "22023": "invalid", "P0002": "stale",
                        "23505": "stale", "23514": "stale"}.get(error.state, "unavailable")
                message = f"premise.{code}"
                self._error(_PREMISE_ERRORS[message], message)
            elif self.path in {_GOAL_SELECT_ROUTE, _GOAL_RELATED_ROUTE}:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "goalReview.unavailable")
            elif self.path == _INTAKE_REVIEW_CONFIRM_ROUTE:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "intakeReview.unavailable")
            elif self.path == _PROJECT_CANVAS_ROUTE:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "project_canvas.unavailable")
            elif self.path == "/api/claim-code":
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "claim_code.save_failed")
            elif self.path in _LIBRARY_WRITES:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "libraryEdit.unavailable")
            elif self.path in _PROJECT_WRITES or self.path in _INTAKE_PUBLIC:
                self._error(HTTPStatus.CONFLICT, "intake.save_failed")
            elif error.state == "23514" or (self.path == "/api/review" and not error.retryable):
                self._error(HTTPStatus.CONFLICT, "The record has changed; refresh and try again.")
            else:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Could not send guidance to this project.")
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        except Exception as error:
            _LOG.error("Workbench write failed (%s).", type(error).__name__)
            if self.path in {_PROOF_DRAFT_ROUTE, _PROOF_DRAFT_DISCARD}:
                message = "proof_draft_unavailable"
            elif self.path in _PROOF_WRITES:
                message = "proof_task.unavailable"
            elif self.path in _AUTOMATION_WRITES:
                message = "automation.unavailable"
            elif self.path == _LEMMA_PLAN_ROUTE:
                message = "lemmaPlan.unavailable"
            elif self.path in _PREMISE_WRITES:
                message = "premise.unavailable"
            elif self.path in {_GOAL_SELECT_ROUTE, _GOAL_RELATED_ROUTE}:
                message = "goalReview.unavailable"
            elif self.path == _INTAKE_REVIEW_CONFIRM_ROUTE:
                message = "intakeReview.unavailable"
            elif self.path == _PROJECT_CANVAS_ROUTE:
                message = "project_canvas.unavailable"
            elif self.path == "/api/claim-code":
                message = "claim_code.save_failed"
            elif self.path in _LIBRARY_WRITES:
                message = "libraryEdit.unavailable"
            elif self.path in _PROJECT_WRITES or self.path in _INTAKE_PUBLIC:
                message = "intake.save_failed"
            elif self.path in _CONVERSATION_WRITES or self.path in _CONVERSATION_INFERENCE:
                message = "conversation.unavailable"
            else:
                message = "Could not send guidance to this project."
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, message)

    def _conversation_error(self, code):
        status = {
            "conversation.invalid": HTTPStatus.BAD_REQUEST,
            "conversation.identity": HTTPStatus.FORBIDDEN,
            "conversation.missing": HTTPStatus.NOT_FOUND,
            "conversation.unavailable": HTTPStatus.SERVICE_UNAVAILABLE,
        }.get(code, HTTPStatus.CONFLICT)
        self._error(status, code)


def _check_schema(store):
    installed = store.query("human", """
        SELECT to_regprocedure('rm.proof_graph_basis_impact(uuid,uuid)') IS NOT NULL AS installed
    """)[0]["installed"]
    if not installed:
        raise _MissingSchema("The workbench requires database revision 0069. See docs/proof-workbench.md.")


def serve(workspace, *, claim_id=None, port=0, open_browser=True):
    if not 0 <= port <= 65535:
        raise ValueError("Choose a port from 0 to 65535.")
    with WorkbenchServer(workspace, claim_id, port) as server:
        server.read(_check_schema)
        if claim_id is not None:
            server.read(proof_graph.snapshot, claim_id)
        print(f"Research Multiplier workbench: {server.launch_url}", flush=True)
        print("Press Ctrl+C to close the workbench.", flush=True)
        if open_browser:
            webbrowser.open(server.launch_url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
