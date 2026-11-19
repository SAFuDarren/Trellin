"""Bounded HTTP transport for model adapters."""

from concurrent.futures import Future, TimeoutError as FutureTimeout
from http.client import HTTPConnection, HTTPException, HTTPSConnection
import json
from socket import SHUT_RDWR
from ssl import SSLCertVerificationError
from threading import Lock, Thread
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.request import HTTPHandler, HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from .model_errors import ModelFailure


MAX_RESPONSE_BYTES = 1_048_576


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def post_json(endpoint, payload, headers, timeout, *, use_proxy=True):
    deadline = _RequestDeadline(timeout)
    request = Request(endpoint, data=json.dumps(payload, allow_nan=False, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST")
    completion = Future()

    def perform():
        try:
            completion.set_result(_read_response(request, deadline, use_proxy))
        except Exception as error:
            completion.set_exception(error)

    Thread(target=perform, name="rm-model-http", daemon=True).start()
    try:
        raw = completion.result(timeout=deadline.remaining())
    except (TimeoutError, FutureTimeout):
        deadline.cancel()
        raise ModelFailure("timeout", "Model endpoint exceeded the request deadline.", retryable=True) from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ModelFailure("budget_exceeded", "Model response exceeded the 1 MiB limit.")
    try:
        reply = json.loads(raw)
    except (ValueError, RecursionError):
        raise ModelFailure("invalid_output", "Model endpoint returned an invalid response.") from None
    if not isinstance(reply, dict):
        raise ModelFailure("invalid_output", "Model endpoint response is not an object.")
    return reply


class _RequestDeadline:
    def __init__(self, timeout):
        self.end = monotonic() + timeout
        self.lock = Lock()
        self.socket = None

    def remaining(self):
        remaining = self.end - monotonic()
        if remaining <= 0:
            raise TimeoutError
        return remaining

    def watch(self, connection_socket):
        with self.lock:
            self.remaining()
            self.socket = connection_socket
            connection_socket.settimeout(self.remaining())

    def cancel(self):
        with self.lock:
            if self.socket is not None:
                try:
                    self.socket.shutdown(SHUT_RDWR)
                except OSError:
                    pass


def _model_unavailable(error):
    """Read only a bounded provider error to classify an unavailable model."""
    if error.code not in {400, 404}:
        return False
    try:
        document = json.loads(error.read(8192))
    except (OSError, HTTPException, ValueError, RecursionError):
        return False
    details = document.get("error") if isinstance(document, dict) else None
    return isinstance(details, dict) and (
        details.get("code") == "model_not_found"
        or (error.code == 404 and details.get("type") == "not_found_error")
    )


def _read_response(request, deadline, use_proxy):
    # urllib retains system proxy and TLS configuration. Track the socket so
    # a peer sending headers or body fragments cannot extend the wall budget.
    class DeadlineConnection:
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            create_connection = self._create_connection

            def connect_socket(address, timeout, source_address):
                connection_socket = create_connection(address, timeout, source_address)
                try:
                    deadline.watch(connection_socket)
                except OSError:
                    connection_socket.close()
                    raise
                return connection_socket

            self._create_connection = connect_socket

        def connect(self):
            self.timeout = deadline.remaining()
            super().connect()
            deadline.watch(self.sock)

        def send(self, data):
            if self.sock is not None:
                deadline.watch(self.sock)
            super().send(data)

    class TimedHTTPConnection(DeadlineConnection, HTTPConnection):
        pass

    class TimedHTTPSConnection(DeadlineConnection, HTTPSConnection):
        pass

    class TimedHTTPHandler(HTTPHandler):
        def http_open(self, request):
            return self.do_open(TimedHTTPConnection, request)

    class TimedHTTPSHandler(HTTPSHandler):
        def https_open(self, request):
            return self.do_open(TimedHTTPSConnection, request, context=self._context)

    handlers = [NoRedirect(), TimedHTTPHandler(), TimedHTTPSHandler()]
    if not use_proxy:
        handlers.append(ProxyHandler({}))
    opener = build_opener(*handlers)
    try:
        with opener.open(request, timeout=deadline.remaining()) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            deadline.remaining()
            return raw
    except HTTPError as error:
        with error:
            status = error.code
            message = f"Model endpoint returned HTTP {status}."
            if status in {401, 403}:
                raise ModelFailure("authentication", message) from None
            if status == 429:
                header = (error.headers.get("Retry-After") or "").strip() if error.headers else ""
                wait = int(header) if header.isascii() and header.isdigit() and len(header) <= 9 else None
                raise ModelFailure("rate_limit", message, retryable=True, retry_after_seconds=wait) from None
            if status in {408, 425, 500, 502, 503, 504}:
                raise ModelFailure("transport", message, retryable=True) from None
            if _model_unavailable(error):
                raise ModelFailure("model_unavailable", "The selected model is unavailable from the endpoint.") from None
            raise ModelFailure("request_rejected", message) from None
    except URLError as error:
        if isinstance(error.reason, TimeoutError):
            raise TimeoutError from None
        if isinstance(error.reason, SSLCertVerificationError):
            raise ModelFailure("configuration", "Model endpoint TLS certificate was rejected.") from None
        raise ModelFailure("transport", "Model endpoint could not complete the request.", retryable=True) from None
    except TimeoutError:
        raise
    except HTTPException:
        raise ModelFailure("invalid_output", "Model endpoint could not complete the HTTP response.") from None
    except OSError:
        raise ModelFailure("transport", "Model endpoint could not complete the request.", retryable=True) from None
