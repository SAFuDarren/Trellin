from contextvars import copy_context
from hashlib import sha256
from io import BytesIO, TextIOWrapper
import json
import os
from pathlib import Path
import sys

import pytest

from research_multiplier import model_adapters
from research_multiplier.models.model_errors import ModelFailure
from research_multiplier.policy import project_budget
from research_multiplier.storage import paths
from research_multiplier.workbench import desktop
from research_multiplier.workflows import desktop_intake, desktop_intake_draft, desktop_pdf, workbench_intake


pytestmark = [pytest.mark.offline, pytest.mark.skipif(os.name != "posix", reason="Native POSIX launcher.")]
_MODEL = "fixture:latest"
_NOTE = "A unit is a positive quantity.\n"
_PAGE = "Each unit has a positive square."
_ITEM = {"id": "i1", "kind": "definition", "label": "Unit", "passage": "p1", "quote": "a positive quantity."}
_SECOND = {"id": "i2", "kind": "theorem", "label": "Positivity", "passage": "p3", "quote": _PAGE}
_LINK = {"from": "i2", "to": "i1"}


def _output(**overrides):
    # A malformed item must fail on its own, without an unrelated dangling link.
    links = [] if "items" in overrides else [_LINK]
    return {"title": "Units", "question": "When are squares positive?",
            "items": [_ITEM, _SECOND], "links": links} | overrides


def _command(monkeypatch, capsys, name, request):
    raw = request if isinstance(request, bytes) else json.dumps(request).encode()
    with TextIOWrapper(BytesIO(raw)) as stdin:
        monkeypatch.setattr(sys, "stdin", stdin)
        monkeypatch.setattr(sys, "argv", ["desktop", "--intake-" + name])
        status = desktop.main()
    streams = capsys.readouterr()
    assert not streams.err
    reply = json.loads(streams.out)
    assert status == (0 if reply["status"] == "ready" else 2)
    assert reply.pop("version") == 1
    return reply


@pytest.fixture
def selected(tmp_path, monkeypatch):
    root = tmp_path.resolve() / "private-folder"
    root.mkdir()
    raw_sources = {"private-notes.md": _NOTE.encode(), "private-pages.pdf": b"fixture pdf bytes"}
    for name, raw in raw_sources.items():
        (root / name).write_bytes(raw)
    selection = desktop_intake.inspect(str(root))

    def extract(raw, budget, timeout):
        assert raw == raw_sources["private-pages.pdf"]
        return json.dumps({"status": "available", "page_count": 8, "pages_included_through": 4,
                           "pages": [{"page": 2, "text": "Background."}, {"page": 4, "text": _PAGE}]}).encode()

    monkeypatch.setattr(desktop_pdf, "bundled_extractor", lambda: extract)
    return {"root": str(root), "identity": selection["identity"], "model": _MODEL,
            "references": [{"path": name, "sha256": sha256(raw).hexdigest()} for name, raw in raw_sources.items()]}


@pytest.fixture
def local(monkeypatch):
    monkeypatch.setattr(workbench_intake, "_catalog", lambda: [{"name": _MODEL, "size": 1}])

    def show(url, document, headers, timeout, *, use_proxy):
        assert url == "http://127.0.0.1:11434/api/show" and not use_proxy
        assert document == {"model": _MODEL + ":local"}
        return {"capabilities": ["completion"], "details": {"family": "fixture"}}

    monkeypatch.setattr(workbench_intake, "post_json", show)
    calls = []

    def configure(reply):
        def post(url, payload, headers, timeout, *, use_proxy):
            assert paths.active_workspace() is None
            assert url == "http://127.0.0.1:11434/api/chat" and not use_proxy
            assert payload["model"] == _MODEL + ":local" and payload["format"] == "json"
            calls.append(json.loads(payload["messages"][1]["content"]))
            if isinstance(reply, Exception):
                raise reply
            return {"model": _MODEL + ":local", "done": True, "done_reason": "stop",
                    "message": {"content": reply if isinstance(reply, str) else json.dumps(reply)}}
        monkeypatch.setattr(model_adapters, "post_json", post)
        return calls
    return configure


def _snapshot(directory):
    return {p.relative_to(directory): (p.stat().st_mtime_ns, p.read_bytes() if p.is_file() else None)
            for p in (directory, *directory.rglob("*"))}


@pytest.mark.parametrize("ambient", ["environment", "cwd"])
def test_launcher_one_completion_maps_real_preview_pages_without_writes(
        tmp_path, selected, local, monkeypatch, capsys, ambient):
    workspace = tmp_path.resolve() / "existing-project"
    workspace.mkdir()
    (workspace / paths.WORKSPACE_FILE).write_text('{"format_version": 1}')
    (workspace / paths.DATABASE_FILE).write_text("bbd1c269-94cf-4939-b891-97eaa0a7d580")
    if ambient == "environment":
        monkeypatch.setenv("RM_DATA_ROOT", str(workspace))
    else:
        monkeypatch.delenv("RM_DATA_ROOT", raising=False)
        monkeypatch.chdir(workspace)
    monkeypatch.setenv("RM_AGENT_DSN", "unused")
    monkeypatch.setenv("RM_TRELLIN_HOME", str(tmp_path / "library"))
    monkeypatch.setattr(project_budget, "Store", lambda: pytest.fail("ambient database access"))
    monkeypatch.setattr(desktop, "Library", lambda: pytest.fail("library access"))
    before = _snapshot(tmp_path)
    calls = local(_output())
    assert _command(monkeypatch, capsys, "models", b"") == {"status": "ready", "models": [_MODEL]}
    reply = _command(monkeypatch, capsys, "draft", selected)
    note, pdf = selected["references"]
    assert reply == {"status": "ready", "draft": {
        "title": "Units", "question": "When are squares positive?",
        "sources": [note, pdf | {"page_count": 8, "pages_included_through": 4}],
        "items": [{"id": "i1", "kind": "definition", "label": "Unit", "source": note | {"quote": _ITEM["quote"]}},
                  {"id": "i2", "kind": "theorem", "label": "Positivity", "source": pdf | {"page": 4, "quote": _PAGE}}],
        "links": [_LINK | {"kind": "suggested_dependency"}]}}
    assert calls == [{"passages": [{"id": "p1", "text": _NOTE}, {"id": "p2", "text": "Background."},
                                    {"id": "p3", "text": _PAGE}]}]
    assert _snapshot(tmp_path) == before and paths.active_workspace() == workspace


def test_suppression_is_nested_context_local_and_restored_on_error(tmp_path):
    workspace = tmp_path.resolve() / "existing"
    paths.initialize_workspace(workspace, minimal=True)
    with paths.use_workspace(workspace, strict=True):
        outer = copy_context()
        with pytest.raises(RuntimeError), paths.without_workspace():
            with paths.without_workspace():
                assert paths.active_workspace() is None
                assert outer.run(paths.active_workspace) == workspace
            assert paths.active_workspace() is None
            raise RuntimeError
        assert paths.require_workspace() == workspace and paths.strict_workspace()


def test_empty_map_preserves_coverage(selected, local):
    local(_output(items=[], links=[]))
    result = desktop_intake_draft.draft(**selected)
    assert result["items"] == result["links"] == []
    assert len(result["sources"]) == 2


@pytest.mark.parametrize("change,code", [
    ("unpinned", "storage-invalid"), ("duplicate-path", "storage-invalid"), ("root-replaced", "intake-changed"),
    ("stale", "intake-changed"), ("missing", "intake-unavailable"), ("pdf-unavailable", "intake-unavailable"),
])
def test_invalid_selection_never_reaches_model(selected, monkeypatch, capsys, change, code):
    monkeypatch.setattr(workbench_intake, "_catalog", lambda: pytest.fail("model inventory on bad selection"))
    monkeypatch.setattr(model_adapters, "post_json", lambda *a, **kw: pytest.fail("model call"))
    path = Path(selected["root"]) / selected["references"][1]["path"]
    if change == "unpinned":
        del selected["references"][1]["sha256"]
    elif change == "duplicate-path":
        selected["references"][1] = selected["references"][0]
    elif change == "root-replaced":
        path.parent.rename(path.parent.with_name("original"))
        path.parent.mkdir()
    elif change == "stale":
        path.write_bytes(b"changed bytes")
    elif change == "missing":
        path.unlink()
    else:
        monkeypatch.setattr(desktop_pdf, "bundled_extractor", lambda: None)
    assert _command(monkeypatch, capsys, "draft", selected) == {"status": "error", "code": code}


@pytest.mark.parametrize("payload", [b'{"root":"a","identity":{},"model":"x","references":[{"path":"a","path":"b"}]}', b'{"model":null}', b'[]', b' ' * 65537])
def test_launcher_rejects_malformed_requests(monkeypatch, capsys, payload):
    assert _command(monkeypatch, capsys, "draft", payload) == {"status": "error", "code": "storage-invalid"}


@pytest.mark.parametrize("row,details", [
    (None, None), ({"name": _MODEL, "size": 1, "remote_model": "remote"}, None),
    ({"name": _MODEL, "size": 1}, {"remote_host": "https://example.invalid", "capabilities": ["completion"],
                                    "details": {"family": "fixture"}}),
    ({"name": _MODEL, "size": 1}, {"capabilities": ["embedding"], "details": {"family": "fixture"}}),
])
def test_unavailable_or_remote_model_never_receives_passages(selected, monkeypatch, capsys, row, details):
    monkeypatch.setattr(workbench_intake, "_catalog", lambda: [row] if row else [])
    monkeypatch.setattr(workbench_intake, "post_json", lambda *a, **kw: details)
    monkeypatch.setattr(model_adapters, "post_json", lambda *a, **kw: pytest.fail("model call"))
    assert _command(monkeypatch, capsys, "draft", selected) == {"status": "error", "code": "intake-model-unavailable"}


@pytest.mark.parametrize("output", [
    _output(transcript="extra"), _output(title=" "), _output(question="x" * 4001), _output(items="bad"),
    _output(items=[_ITEM] * 16), _output(items=[_ITEM, _ITEM]), _output(items=[_ITEM | {"id": "1"}]),
    _output(items=[_ITEM | {"kind": []}]), _output(items=[_ITEM | {"label": "\0"}]),
    _output(items=[_ITEM | {"page": 4}]), _output(items=[_ITEM | {"passage": "p9"}]),
    _output(items=[_ITEM | {"passage": "p3"}]), _output(items=[_SECOND | {"passage": "p2"}]),
    _output(items=[_ITEM | {"quote": "a positive\nquantity."}]), _output(items=[_ITEM | {"quote": " "}]),
    _output(links=[{"from": "i1", "to": "i3"}]), _output(links=[{"from": "i1", "to": "i1"}]),
    _output(links=[_LINK, _LINK]), _output(links=[_LINK | {"kind": "confirmed"}]), _output(links=[_LINK] * 31),
    '{"title":"secret","title":"duplicate"}', 'not JSON',
])
def test_invalid_model_response_is_fixed_and_no_partial_result(selected, local, monkeypatch, capsys, output):
    calls = local(output)
    assert _command(monkeypatch, capsys, "draft", selected) == {"status": "error", "code": "intake-model-output"}
    assert len(calls) == 1


def test_provider_failure_sanitized_without_retry(selected, local, monkeypatch, capsys):
    calls = local(ModelFailure("timeout", "sensitive provider diagnostic"))
    assert _command(monkeypatch, capsys, "draft", selected) == {"status": "error", "code": "intake-model-failed"}
    assert len(calls) == 1
