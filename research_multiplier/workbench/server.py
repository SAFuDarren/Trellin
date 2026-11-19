"""Serve a project-bound proof graph on the IPv4 loopback interface."""

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
import logging
import secrets
import threading
import time
from urllib.parse import parse_qs, urlsplit
from uuid import UUID
import webbrowser

from research_multiplier.cli._output import json_default
from research_multiplier.proof.lean_watch import LakeWatch
from research_multiplier.storage import paths
from research_multiplier.storage.postgres import DatabaseOperationError, Store
from research_multiplier.workflows import (doctor, proof_graph, research_progress, workbench_activity,
                                          workbench_guidance, workbench_intake, workbench_proof_tasks,
                                          workbench_sections, workbench_theorems)


# The interface is built from web/ into the package (docs/engineering.md); only its exact files are served.
_APP_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2", ".json": "application/json; charset=utf-8",
}
_POLICY = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
           "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
_LOG = logging.getLogger(__name__)
_MAX_GUIDANCE_BYTES = 16_384
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


class _BadRequest(ValueError):
    pass


class _MissingAssets(ValueError):
    pass


class _MissingSchema(ValueError):
    pass


def _uuid_parameters(query, *fields):
    try:
        values = parse_qs(query, keep_blank_values=True, strict_parsing=True, max_num_fields=len(fields))
        if values.keys() != set(fields) or any(len(value) != 1 for value in values.values()):
            raise ValueError
        return tuple(UUID(values[field][0]) for field in fields)
    except (ValueError, KeyError):
        raise _BadRequest("Supply one valid UUID for each requested reference.") from None


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
        self._proof_task_closing = threading.Event()
        self._running_policy = None
        self._failed_policy = None
        super().__init__(("127.0.0.1", port), _Handler)
        self.host = f"127.0.0.1:{self.server_port}"
        self.origin = f"http://{self.host}"

    @property
    def launch_url(self):
        return f"{self.origin}/#session={self.session}"

    def server_close(self):
        self._proof_task_closing.set()
        super().server_close()

    def read(self, action, *values):
        # Request threads do not inherit the CLI's workspace ContextVar.
        with paths.use_workspace(self.workspace, strict=True), Store() as store:
            return store.transaction("human", lambda: action(store, *values), read_only=True)

    def write(self, action, *values):
        with paths.use_workspace(self.workspace, strict=True), Store() as store:
            return store.transaction("human", lambda: action(store, *values))

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
                while not self._proof_task_closing.is_set():
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


class _Handler(BaseHTTPRequestHandler):
    server_version = "ResearchMultiplier/1.0"

    def setup(self):
        self.request.settimeout(10)
        super().setup()

    def log_message(self, *_):
        pass  # Do not record project URLs, IDs or request headers.

    def _send(self, status, content, body):
        self.send_response(status)
        self.send_header("Content-Type", content)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", _POLICY)
        self.end_headers()
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
        if route.path == "/api/project-intake/models":
            if route.query:
                raise _BadRequest("This endpoint does not take parameters.")
            return workbench_intake.models()
        if route.path == "/api/claims":
            if route.query:
                raise _BadRequest("This endpoint does not take parameters.")
            return {"claims": self.server.read(proof_graph.claims), "claim_id": self.server.claim_id}
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
        return None

    def do_GET(self):
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
            if not isinstance(body, dict) or body.keys() != {"evidence_id", "action", "actor", "reason"}:
                raise ValueError
            actor, reason = body["actor"].strip(), body["reason"].strip()
            if body["action"] not in ("approve", "return") or not 0 < len(actor) <= 120 or not 0 < len(reason) <= 2000:
                raise ValueError
            return UUID(body["evidence_id"]), body["action"], actor, reason
        except (ValueError, AttributeError, TypeError):
            raise _BadRequest("Supply the evidence, approve or return, your name and a reason.") from None

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
            elif self.path in _PROJECT_WRITES:
                document = self._body(_MAX_PROJECT_BYTES)
                if not isinstance(document, dict):
                    raise _BadRequest("intake.invalid")
                self._json(HTTPStatus.OK, self.server.write_project(_PROJECT_WRITES[self.path], document))
            else:
                self._error(HTTPStatus.NOT_FOUND, "Page unavailable.")
        except _BadRequest as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
        except workbench_guidance.GuidanceError as error:
            self._error(HTTPStatus.CONFLICT, str(error))
        except workbench_proof_tasks.ProofTaskError as error:
            status = HTTPStatus.BAD_REQUEST if error.code == "proof_task.invalid" else HTTPStatus.CONFLICT
            self._error(status, error.code)
        except (workbench_intake.IntakeError, workbench_theorems.TheoremError) as error:
            self._error(HTTPStatus.CONFLICT, str(error))
        except DatabaseOperationError as error:
            if self.path in _PROOF_WRITES:
                status = HTTPStatus.SERVICE_UNAVAILABLE if error.retryable else HTTPStatus.CONFLICT
                self._error(status, "proof_task.unavailable" if error.retryable else "proof_task.stale")
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
