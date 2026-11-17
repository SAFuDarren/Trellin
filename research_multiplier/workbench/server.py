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
from research_multiplier.storage import paths
from research_multiplier.storage.postgres import DatabaseOperationError, Store
from research_multiplier.workflows import (doctor, proof_graph, workbench_activity, workbench_guidance,
                                          workbench_sections)


_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/workbench.css": ("workbench.css", "text/css; charset=utf-8"),
    "/workbench.js": ("workbench.js", "text/javascript; charset=utf-8"),
}
_POLICY = ("default-src 'none'; script-src 'self'; style-src 'self'; "
           "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
_LOG = logging.getLogger(__name__)
_MAX_GUIDANCE_BYTES = 16_384
_DOCTOR_SECONDS = 120
# Each workspace-wide page takes no parameters.
_SECTIONS = {
    "/api/inbox": workbench_sections.inbox,
    "/api/library": workbench_sections.library,
    "/api/team": workbench_sections.team,
    "/api/progress": workbench_sections.progress,
    "/api/threads": workbench_sections.threads,
}


class _BadRequest(ValueError):
    pass


def _uuid_parameters(query, *fields):
    try:
        values = parse_qs(query, keep_blank_values=True, strict_parsing=True, max_num_fields=len(fields))
        if values.keys() != set(fields) or any(len(value) != 1 for value in values.values()):
            raise ValueError
        return tuple(UUID(values[field][0]) for field in fields)
    except (ValueError, KeyError):
        raise _BadRequest("Supply one valid UUID for each requested reference.") from None


class WorkbenchServer(ThreadingHTTPServer):
    def __init__(self, workspace, claim_id=None, port=0):
        self.workspace = workspace
        self.claim_id = claim_id
        self.session = secrets.token_urlsafe(32)
        directory = files("research_multiplier.workbench").joinpath("assets")
        self.assets = {path: (directory.joinpath(name).read_bytes(), mime)
                       for path, (name, mime) in _ASSETS.items()}
        self._doctor = (0.0, None)
        self._doctor_lock = threading.Lock()
        super().__init__(("127.0.0.1", port), _Handler)
        self.host = f"127.0.0.1:{self.server_port}"
        self.origin = f"http://{self.host}"

    @property
    def launch_url(self):
        return f"{self.origin}/#session={self.session}"

    def read(self, action, *values):
        # Request threads do not inherit the CLI's workspace ContextVar.
        with paths.use_workspace(self.workspace, strict=True), Store() as store:
            return store.transaction("human", lambda: action(store, *values), read_only=True)

    def write(self, action, *values):
        with paths.use_workspace(self.workspace, strict=True), Store() as store:
            return store.transaction("human", lambda: action(store, *values))

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
        except DatabaseOperationError as error:
            if error.state in {"42883", "42P01", "42703"}:
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

    def _body(self):
        if (self.headers.get_content_type() != "application/json" or self.headers.get_all("Transfer-Encoding")
                or self.headers.get_all("Content-Encoding")):
            raise _BadRequest("Send a UTF-8 JSON message.")
        lengths = self.headers.get_all("Content-Length", ())
        try:
            length = int(lengths[0]) if len(lengths) == 1 and lengths[0].isascii() and lengths[0].isdigit() else 0
            if not 0 < length <= _MAX_GUIDANCE_BYTES:
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
            else:
                self._error(HTTPStatus.NOT_FOUND, "Page unavailable.")
        except _BadRequest as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
        except workbench_guidance.GuidanceError as error:
            self._error(HTTPStatus.CONFLICT, str(error))
        except DatabaseOperationError as error:
            if error.state == "23514" or (self.path == "/api/review" and not error.retryable):
                self._error(HTTPStatus.CONFLICT, "记录已改变，请刷新后重试。")
            else:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Could not send guidance to this project.")
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        except Exception as error:
            _LOG.error("Workbench guidance failed (%s).", type(error).__name__)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "Could not send guidance to this project.")


def _check_schema(store):
    installed = store.query("human", """
        SELECT to_regprocedure('rm.proof_graph_basis_impact(uuid,uuid)') IS NOT NULL AS installed
    """)[0]["installed"]
    if not installed:
        raise ValueError("The workbench requires database revision 0069. See docs/proof-workbench.md.")


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
