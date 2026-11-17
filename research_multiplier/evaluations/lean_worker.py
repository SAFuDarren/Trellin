"""Versioned subprocess transport for isolated Lean evaluations."""

from datetime import datetime
from decimal import Decimal
from importlib import import_module
import json
import os
from pathlib import Path
import sys
from uuid import UUID


PROTOCOL_VERSION = 1
_ACTIONS = {"inspect", "run", "read"}


def command(installation, implementation=None):
    args = [sys.executable, "-I", "-B", str(Path(__file__).resolve()), str(installation)]
    if implementation is not None:
        args.append(implementation)
    return args


def request_text(data):
    return json.dumps({"protocol_version": PROTOCOL_VERSION, "request": data},
                      ensure_ascii=False, allow_nan=False)


def read_response(stdout, returncode):
    if returncode != 0:
        raise ValueError(f"The isolated Lean shadow worker exited with status {returncode}.")
    try:
        response = json.loads(stdout)
    except ValueError:
        raise ValueError("The isolated Lean shadow worker returned invalid JSON.") from None
    if (not isinstance(response, dict)
            or type(response.get("protocol_version")) is not int
            or response["protocol_version"] != PROTOCOL_VERSION):
        raise ValueError("The isolated Lean shadow worker uses an incompatible protocol version.")
    fields = set(response) - {"protocol_version"}
    if fields == {"error"} and isinstance(response["error"], str):
        return {"error": response["error"]}
    if fields == {"result"} and (response["result"] is None or isinstance(response["result"], dict)):
        return {"result": response["result"]}
    raise ValueError("The isolated Lean shadow worker returned an incompatible protocol result.")


def _read_request(stream):
    try:
        envelope = json.load(stream)
    except ValueError:
        raise ValueError("The Lean shadow worker request is invalid JSON.") from None
    if (not isinstance(envelope, dict) or set(envelope) != {"protocol_version", "request"}
            or type(envelope["protocol_version"]) is not int or envelope["protocol_version"] != PROTOCOL_VERSION):
        raise ValueError("The Lean shadow worker request uses an incompatible protocol version.")
    request = envelope["request"]
    if (not isinstance(request, dict) or not isinstance(request.get("action"), str)
            or request["action"] not in _ACTIONS or not isinstance(request.get("workspace"), str)
            or any(not isinstance(request.get(name), dict) for name in ("identity", "scope", "options"))
            or request["action"] == "run" and not isinstance(request.get("starting_state"), dict)):
        raise ValueError("The Lean shadow worker request has an invalid action or proof context.")
    return request


def _encode(value):
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Cannot encode {type(value).__name__}")


def _selected_worker(root):
    package = import_module("research_multiplier")
    if Path(package.__file__).resolve() != root / "research_multiplier" / "__init__.py":
        raise ValueError("The selected Lean shadow installation was not loaded.")
    # The old wheel keeps its handler at the previous physical path. The
    # parent owns the wire protocol for both snapshot layouts.
    name = "research_multiplier.evaluations.lean_shadow"
    if not (root / "research_multiplier" / "evaluations" / "lean_shadow.py").is_file():
        name = "research_multiplier.lean_shadow"
    return import_module(name)


def _run(root, expected, data):
    sys.path.insert(0, str(root))
    sys.path.extend(path for path in os.environ.get("PYTHONPATH", "").split(os.pathsep) if path)
    worker = _selected_worker(root)
    if expected is None:
        return worker._child(data)
    storage = ("research_multiplier.storage" if worker.__name__ == "research_multiplier.evaluations.lean_shadow"
               else "research_multiplier")
    paths = import_module(f"{storage}.paths")
    postgres = import_module(f"{storage}.postgres")

    def check_binding():
        if worker.implementation_sha256() != expected:
            raise ValueError("The isolated Lean shadow installation changed.")
        try:
            with paths.use_workspace(data["workspace"], strict=True), postgres.Store() as store:
                [schema] = store.query("verifier", "SELECT rm.lean_shadow_schema_revision() AS revision")
        except postgres.DatabaseOperationError as error:
            if error.state != "42883":
                raise
            raise ValueError(
                "Restore the same PostgreSQL schema revision on the source and both shadow sides."
            ) from None
        if schema["revision"] != data.get("database_schema_revision"):
            raise ValueError("Restore the same PostgreSQL schema revision on the source and both shadow sides.")

    check_binding()
    result = worker._child(data)
    check_binding()
    return result


def main():
    if len(sys.argv) not in (2, 3):
        raise SystemExit("Expected a Lean shadow installation and optional implementation digest.")
    root = Path(sys.argv[1]).resolve()
    expected = sys.argv[2] if len(sys.argv) == 3 else None
    try:
        response = {"result": _run(root, expected, _read_request(sys.stdin))}
    except (OSError, ValueError) as error:
        response = {"error": str(error)}
    print(json.dumps({"protocol_version": PROTOCOL_VERSION, **response},
                     ensure_ascii=False, allow_nan=False, default=_encode))


if __name__ == "__main__":
    main()
