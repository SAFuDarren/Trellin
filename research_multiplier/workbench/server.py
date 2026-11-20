"""Serve a project-bound proof graph on the IPv4 loopback interface."""

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
import logging
import secrets
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4
import webbrowser

from research_multiplier.cli._output import json_default
from research_multiplier.proof.lean_watch import LakeWatch
from research_multiplier.storage import paths
from research_multiplier.storage.postgres import DatabaseOperationError, Store
from research_multiplier.workbench.runtime_updates import RuntimeUpdates
from research_multiplier.workflows import (claim_references, doctor, proof_graph, release_updates, research_progress,
                                          workbench_activity, workbench_clarification, workbench_guidance,
                                          workbench_intake, workbench_proof_tasks, workbench_review, workbench_sections,
                                          workbench_theorems, workspace_events)


# The interface is built from web/ into the package (docs/engineering.md); only its exact files are served.
_APP_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2", ".json": "application/json; charset=utf-8",
}
_POLICY = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
           "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
_LOG = logging.getLogger(__name__)
_CLAIMS_ROUTE = "/api/v1/workspaces/{workspace_id}/claims"
_ACCESS_LOG_LOCK = threading.Lock()
_MAX_GUIDANCE_BYTES = 16_384
_MAX_CLARIFICATION_BYTES = 64_000
_MAX_PROJECT_BYTES = 240_000
_DOCTOR_SECONDS = 120
_SCHEMA_SQLSTATES = {"42883", "42P01", "42703"}
# Each workspace-wide page takes no parameters.
_SECTIONS = {
    "/api/inbox": workbench_sections.inbox,
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
_PROOF_WRITES = {"/api/proof-task/start", "/api/proof-task/stop"}
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
    "idempotency.conflict": (HTTPStatus.CONFLICT, "This request key was already used for a different review."),
    "database.unavailable": (HTTPStatus.SERVICE_UNAVAILABLE, "The selected research database is unavailable."),
    "request.failed": (HTTPStatus.INTERNAL_SERVER_ERROR, "Could not complete the workbench request."),
    "events.not_found": (HTTPStatus.NOT_FOUND, "The requested workspace event stream is unavailable."),
    "events.invalid_cursor": (HTTPStatus.BAD_REQUEST, "The event cursor is invalid for this workspace."),
    "events.unavailable": (HTTPStatus.CONFLICT, "Upgrade the project database to use workspace events."),
    "events.identity_required": (HTTPStatus.FORBIDDEN, "Bind the workspace to resolve a local user."),
    "claims.invalid_query": (HTTPStatus.BAD_REQUEST, "The recent claims list does not take parameters."),
    "claims.not_found": (HTTPStatus.NOT_FOUND, "The requested workspace claims are unavailable."),
}


class _BadRequest(ValueError):
    pass


class _MissingAssets(ValueError):
    pass


class _MissingSchema(ValueError):
    pass


class _MissingClaimsWorkspace(ValueError):
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
        self._proof_task_lock = threading.Lock()
        self.closing = threading.Event()
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
        super().server_close()

    def read(self, action, *values):
        # Request threads do not inherit the CLI's workspace ContextVar.
        with paths.use_workspace(self.workspace, strict=True), Store() as store:
            return store.transaction("human", lambda: action(store, *values), read_only=True)

    def write(self, action, *values):
        with paths.use_workspace(self.workspace, strict=True), Store() as store:
            return store.transaction("human", lambda: action(store, *values))

    def prepare_clarification(self, workspace_id, identifier, document):
        # The existing workflow locks the request and commits only after the model returns.
        with paths.use_workspace(self.workspace, strict=True), Store() as store:
            return workbench_clarification.prepare(store, workspace_id, identifier, document)

    def write_project(self, action, document):
        if action is not workbench_intake.draft:
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

    def doctor(self, fresh=False):
        """Run the read-only installation check, reusing a recent report unless a fresh one is requested."""
        with self._doctor_lock:
            checked, report = self._doctor
            if fresh or report is None or time.monotonic() - checked > _DOCTOR_SECONDS:
                with paths.use_workspace(self.workspace, strict=True):
                    report = doctor.diagnose()
                self._doctor = (time.monotonic(), report)
            return report

    def add_guidance(self, claim_id, statement_id, request_id, node_id, text, audience, confirmed):
        with paths.use_workspace(self.workspace, strict=True), Store() as store:
            return store.transaction("human", lambda: workbench_guidance.add(
                store, claim_id, statement_id, request_id, node_id, text, audience=audience, confirm_release=confirmed,
            ))

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
            identifier = str(result["policy_id"])
            self._running_policy = identifier
            self._failed_policy = None
            try:
                threading.Thread(target=self._work_on_proof, args=(identifier,), daemon=True).start()
            except RuntimeError:
                self._running_policy = None
                self._failed_policy = identifier
                raise workbench_proof_tasks.ProofTaskError("proof_task.worker_failed") from None
            return result

    def _work_on_proof(self, identifier):
        failed = True
        try:
            with paths.use_workspace(self.workspace, strict=True):
                lake_watch = LakeWatch()
                failed = False
                while not self.closing.is_set():
                    cycle = research_progress.advance_cycle("combined", 1, [identifier], 1, lake_watch=lake_watch)
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
    return {**workbench_activity.snapshot(store, claim_id), **workbench_guidance.activity(store, claim_id)}


def _workspace_claims(store, workspace_id):
    if store.workspace_id != workspace_id:
        raise _MissingClaimsWorkspace
    return proof_graph.claim_index(store)


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
        if route.path == "/api/release-updates":
            if route.query:
                raise _BadRequest("This endpoint does not take parameters.")
            return self.server.release_updates.snapshot()
        if route.path == "/api/project-intake/models":
            if route.query:
                raise _BadRequest("This endpoint does not take parameters.")
            return workbench_intake.models()
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
            return self.server.read(proof_graph.snapshot, *_uuid_parameters(route.query, "claim"))
        if route.path == "/api/impact":
            return self.server.read(proof_graph.impact, *_uuid_parameters(route.query, "claim", "basis"))
        if route.path == "/api/activity":
            return self.server.read(_activity, *_uuid_parameters(route.query, "claim"))
        if route.path == "/api/proof-task":
            return self.server.proof_task(*_uuid_parameters(route.query, "claim"))
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
        except workbench_proof_tasks.ProofTaskError as error:
            self._error(HTTPStatus.NOT_FOUND, error.code)
        except claim_references.ClaimCodeError as error:
            self._error(*_claim_code_error(error))
        except DatabaseOperationError as error:
            if error.state in _SCHEMA_SQLSTATES:
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
        claims_path = self.path.split("?", 1)[0].split("#", 1)[0].split("/")
        if len(claims_path) == 6 and claims_path[3] == "workspaces" and claims_path[5] == "claims":
            self._get_claims_v1(claims_path[4], "?" in self.path or "#" in self.path)
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

    def _claims_v1_reply(self, workspace, has_arguments):
        if not self._trusted():
            return "request.forbidden", None
        if not self._authorized():
            return "request.unauthenticated", None
        try:
            workspace_id = UUID(workspace)
            if str(workspace_id) != workspace.lower():
                raise ValueError
        except ValueError:
            return "claims.not_found", None
        if has_arguments:
            return "claims.invalid_query", None
        try:
            index = self.server.read(_workspace_claims, workspace_id)
        except _MissingClaimsWorkspace:
            return "claims.not_found", None
        except ValueError:
            # Store configuration and driver errors can contain sensitive connection details.
            return "database.unavailable", None
        except Exception:
            return "request.failed", None
        payload = {"items": index["claims"], "next_cursor": None, "claim_id": self.server.claim_id}
        if "claim_codes" in index:
            payload["claim_codes"] = index["claim_codes"]
        return None, payload

    def _get_claims_v1(self, workspace, has_arguments):
        start = time.monotonic()
        code, payload = self._claims_v1_reply(workspace, has_arguments)
        status = _V1_ERRORS[code][0] if code else HTTPStatus.OK
        entry = {"route": _CLAIMS_ROUTE, "status": status, "request_id": self.request_id,
                 "duration_ms": round((time.monotonic() - start) * 1000, 3)}
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

    def _post_v1(self):
        self.request_id = str(uuid4())
        try:
            if not self._trusted() or self.headers.get_all("Origin") != [self.server.origin]:
                self._v1_error("request.forbidden")
                return
            if not self._authorized():
                self._v1_error("request.unauthenticated")
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
            if not isinstance(body, dict) or not required <= body.keys() <= required | {"audience", "confirm_release"}:
                raise ValueError
            return (UUID(body["claim_id"]), UUID(body["statement_id"]), UUID(body["request_id"]),
                    body["node_id"], body["text"], body.get("audience", "local"), body.get("confirm_release", False))
        except (ValueError, AttributeError, TypeError, RecursionError):
            raise _BadRequest("Supply one valid guidance message with a claim, a node and a request ID.") from None

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
            elif self.path == "/api/review":
                self._json(HTTPStatus.OK, self.server.write(workbench_sections.review, *self._review_body()))
            elif self.path == "/api/proof-task/start":
                self._json(HTTPStatus.OK, self.server.start_proof_task(self._body()))
            elif self.path == "/api/proof-task/stop":
                self._json(HTTPStatus.OK, self.server.write(workbench_proof_tasks.stop, self._body()))
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
            elif self.path in _PROJECT_WRITES:
                document = self._body(_MAX_PROJECT_BYTES)
                if not isinstance(document, dict):
                    raise _BadRequest("intake.invalid")
                self._json(HTTPStatus.OK, self.server.write_project(_PROJECT_WRITES[self.path], document))
            else:
                self._error(HTTPStatus.NOT_FOUND, "Page unavailable.")
        except _BadRequest as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
        except workbench_review.ReviewError as error:
            if str(error) == "review.legacy_name_required":
                self._error(HTTPStatus.BAD_REQUEST, "Supply the evidence, approve or return, your name and a reason.")
            else:
                self._error(*_V1_ERRORS[str(error)])
        except workbench_guidance.GuidanceError as error:
            self._error(HTTPStatus.CONFLICT, str(error))
        except workbench_proof_tasks.ProofTaskError as error:
            status = HTTPStatus.BAD_REQUEST if error.code == "proof_task.invalid" else HTTPStatus.CONFLICT
            self._error(status, error.code)
        except claim_references.ClaimCodeError as error:
            self._error(*_claim_code_error(error))
        except (workbench_intake.IntakeError, workbench_theorems.TheoremError) as error:
            self._error(HTTPStatus.CONFLICT, str(error))
        except DatabaseOperationError as error:
            if self.path in _PROOF_WRITES:
                status = HTTPStatus.SERVICE_UNAVAILABLE if error.retryable else HTTPStatus.CONFLICT
                self._error(status, "proof_task.unavailable" if error.retryable else "proof_task.stale")
            elif self.path == "/api/claim-code":
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "claim_code.save_failed")
            elif self.path in _PROJECT_WRITES:
                self._error(HTTPStatus.CONFLICT, "intake.save_failed")
            elif error.state == "23514" or (self.path == "/api/review" and not error.retryable):
                self._error(HTTPStatus.CONFLICT, "The record has changed; refresh and try again.")
            else:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Could not send guidance to this project.")
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        except Exception as error:
            _LOG.error("Workbench write failed (%s).", type(error).__name__)
            if self.path in _PROOF_WRITES:
                message = "proof_task.unavailable"
            elif self.path == "/api/claim-code":
                message = "claim_code.save_failed"
            elif self.path in _PROJECT_WRITES:
                message = "intake.save_failed"
            else:
                message = "Could not send guidance to this project."
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, message)


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
