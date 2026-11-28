from hashlib import sha256
import json
import os
import subprocess
import sys

import pytest

from research_multiplier.storage.managed_workspaces import LibraryError
from research_multiplier.workflows import desktop_intake, desktop_pdf, desktop_source
from research_multiplier.workflows.intake import MAX_MODEL_NOTES_BYTES


pytestmark = [pytest.mark.offline, pytest.mark.skipif(os.name != "posix", reason="Native POSIX launcher.")]


def _pages():
    # A page can be blank. Page numbers count physical pages including blanks.
    return {"status": "available", "page_count": 4, "pages_included_through": 3,
            "pages": [{"page": 1, "text": "α"}, {"page": 3, "text": "乙"}]}


def _preview(root, references, worker):
    selection = desktop_intake.inspect(str(root))
    return desktop_intake.preview(str(root), selection["identity"], references, pdf_worker=worker)


def test_selected_pdf_and_text_share_budget_and_preserve_exact_bytes(tmp_path):
    root = tmp_path.resolve()
    pdf = b"%PDF-1.7\r\nsynthetic"
    digest = sha256(pdf).hexdigest()
    (root / "book.PDF").write_bytes(pdf)
    (root / "note.md").write_bytes(b"x" * (MAX_MODEL_NOTES_BYTES - 5))
    (root / "tail.txt").write_bytes(b"z")
    (root / "next.pdf").write_bytes(b"other")
    calls = []

    def worker(raw, budget, timeout):
        calls.append((raw, budget))
        assert 0 < timeout <= desktop_pdf.EXTRACTION_SECONDS
        return json.dumps(_pages()).encode()

    refs = [{"path": "note.md"}, {"path": "book.PDF", "sha256": digest}, {"path": "tail.txt"}, {"path": "next.pdf"}]
    rows = _preview(root, refs, worker)
    assert calls == [(pdf, 5)]
    assert rows[0]["text"] == "x" * (MAX_MODEL_NOTES_BYTES - 5)
    assert rows[1:] == [dict(_pages(), path="book.PDF", sha256=digest),
                        {"path": "tail.txt", "status": "too_large"},
                        {"path": "next.pdf", "status": "pdf_text_limit"}]


def test_stale_and_oversized_pdfs_and_special_files_never_reach_worker(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    monkeypatch.setattr(desktop_pdf, "MAX_RAW_BYTES", 8)
    (root / "stale.pdf").write_bytes(b"one")
    (root / "valid.pdf").write_bytes(b"12345")
    (root / "tail.pdf").write_bytes(b"a")
    (root / "large.pdf").write_bytes(b"x" * 9)
    (root / "link.pdf").symlink_to(root / "valid.pdf")
    os.mkfifo(root / "fifo.pdf")
    calls = []

    def worker(raw, budget, timeout):
        calls.append(raw)
        return b'{"status":"pdf_invalid"}'

    refs = [{"path": "stale.pdf", "sha256": "0" * 64}, {"path": "large.pdf"}, {"path": "valid.pdf"},
            {"path": "tail.pdf"}, {"path": "link.pdf"}, {"path": "fifo.pdf"}]
    expected = ["changed", "too_large", "pdf_invalid", "too_large", "unsupported", "unsupported"]
    assert _preview(root, refs, worker) == [
        {"path": ref["path"], "status": status} for ref, status in zip(refs, expected, strict=True)
    ]
    assert calls == [b"12345"]


@pytest.mark.parametrize("change", ["file", "parent", "root"])
def test_pdf_name_and_root_settle_after_extraction(tmp_path, change):
    root = tmp_path.resolve() / "source"
    folder = root / "sub"
    folder.mkdir(parents=True)
    path = folder / "book.pdf"
    path.write_bytes(b"first")

    def worker(raw, budget, timeout):
        assert raw == b"first"
        if change == "file":
            path.rename(folder / "old")
        elif change == "parent":
            folder.rename(root / "old")
            folder.mkdir()
        else:
            root.rename(root.with_name("old"))
            folder.mkdir(parents=True)
        path.write_bytes(b"second")
        return json.dumps(_pages()).encode()

    refs = [{"path": "sub/book.pdf"}]
    if change == "root":
        with pytest.raises(LibraryError, match="intake-changed"):
            _preview(root, refs, worker)
    else:
        assert _preview(root, refs, worker) == [{"path": "sub/book.pdf", "status": "unstable"}]


@pytest.mark.parametrize("delay", ["read", "worker"])
def test_one_absolute_extraction_deadline_includes_reads_and_all_workers(tmp_path, monkeypatch, delay):
    root = tmp_path.resolve()
    refs = [{"path": name} for name in ("first.pdf", "second.pdf", "note.txt")]
    for ref in refs:
        (root / ref["path"]).write_bytes(b"fixture")
    now = [100.0]
    calls = []
    monkeypatch.setattr(desktop_pdf.time, "monotonic", lambda: now[0])
    chunks = desktop_source._file_chunks

    def read(fd, size):
        yield from chunks(fd, size)
        if delay == "read":
            now[0] += desktop_pdf.EXTRACTION_SECONDS

    def worker(raw, budget, timeout):
        calls.append(raw)
        now[0] += desktop_pdf.EXTRACTION_SECONDS
        return json.dumps(_pages()).encode()

    monkeypatch.setattr(desktop_source, "_file_chunks", read)
    rows = _preview(root, refs, worker)
    assert [r["status"] for r in rows] == ["pdf_timeout", "pdf_timeout", "available"]
    assert calls == ([] if delay == "read" else [b"fixture"])


def test_bundled_helper_receives_only_bytes_and_numeric_budget(tmp_path, monkeypatch):
    contents = tmp_path.resolve() / "Renamed.app" / "Contents"
    executable = contents / "Resources" / "python" / "bin" / "python3"
    executable.parent.mkdir(parents=True)
    executable.touch()
    helper = contents / "Helpers" / "trellin-pdf-preview"
    helper.parent.mkdir()
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setenv("PATH", str(tmp_path))
    assert desktop_pdf.bundled_extractor() is None
    helper.write_text("#!/bin/sh\n[ \"$#\" = 1 ] && [ \"$1\" = 5 ] || exit 1\n"
                      '[ "$PWD" = / ] || exit 1\n'
                      "IFS= read -r line\n[ \"$line\" = 'fixture' ] || exit 1\n"
                      "echo 'private diagnostic' >&2\nprintf '%s' '{\"status\":\"pdf_locked\"}'\n")
    helper.chmod(0o700)
    worker = desktop_pdf.bundled_extractor()
    assert worker(b"fixture\n", 5, 2) == b'{"status":"pdf_locked"}'
    monkeypatch.setattr(sys, "platform", "linux")
    assert desktop_pdf.bundled_extractor() is None
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python3"))
    (tmp_path / "python3").symlink_to(executable)
    assert desktop_pdf.bundled_extractor() is None
    monkeypatch.setattr(sys, "executable", str(executable))
    helper.rename(tmp_path / "outside")
    helper.symlink_to(tmp_path / "outside")
    assert desktop_pdf.bundled_extractor() is None


def test_timed_out_helper_is_reaped_and_diagnostic_is_withheld(tmp_path, capfd, monkeypatch):
    helper = tmp_path / "helper"
    helper.write_text("#!/bin/sh\necho 'private diagnostic' >&2\nexec /bin/sleep 5\n")
    helper.chmod(0o700)
    children = []
    popen = subprocess.Popen

    def record(*args, **kwargs):
        child = popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", record)
    with pytest.raises(subprocess.TimeoutExpired):
        desktop_pdf._run_helper(helper, b"fixture", 1, 0.05)
    assert len(children) == 1
    with pytest.raises(ChildProcessError):
        os.waitpid(children[0].pid, os.WNOHANG)
    assert capfd.readouterr() == ("", "")


@pytest.mark.parametrize("raw", [
    b"private diagnostic", b"\xff", b"[]", b'{"status":"pdf_invalid","text":"extra"}',
    b'{"status":{},"status":"pdf_invalid"}', b'{"status":"changed"}', b'{"status":[]}',
    b'{"status":"available"}', b" " * 9000, b"[" * 2000 + b"]" * 2000,
    *[json.dumps(dict(_pages(), **change)).encode() for change in [
        {"page_count": True}, {"page_count": 129}, {"pages_included_through": 0}, {"pages_included_through": 5},
        {"pages": []}, {"pages": [{"page": True, "text": "x"}]}, {"pages": [{"page": 0, "text": "x"}]},
        {"pages": [{"page": 4, "text": "x"}]}, {"pages": [{"page": 2, "text": "z"}, {"page": 2, "text": "y"}]},
        {"pages": [{"page": 1, "text": "  "}]}, {"pages": [{"page": 1, "text": "x\x00"}]}, {"pages": [{"page": 1, "text": "\ud800"}]},
        {"pages": [{"page": 1, "text": "乙乙"}]}, {"pages": [{"page": 1, "text": "a", "path": "other"}]},
    ]],
])
def test_malformed_or_over_budget_protocol_cannot_expose_content(raw):
    result = desktop_pdf.extract(lambda *args: raw, b"fixture", 5, desktop_pdf.time.monotonic() + 5)
    assert result == ({"status": "pdf_unavailable"}, 0)


@pytest.mark.parametrize("status", ["pdf_invalid", "pdf_locked", "pdf_no_text", "pdf_page_limit", "pdf_text_limit"])
def test_structured_helper_errors(status):
    result = desktop_pdf.extract(lambda *args: json.dumps({"status": status}).encode(),
                                 b"fixture", 5, desktop_pdf.time.monotonic() + 5)
    assert result == ({"status": status}, 0)


@pytest.mark.parametrize(("error", "status"), [(OSError("private"), "pdf_unavailable"),
                                              (subprocess.CalledProcessError(1, "private"), "pdf_unavailable"),
                                              (subprocess.TimeoutExpired("private", 1), "pdf_timeout")])
def test_helper_failure_is_structured(error, status):
    def worker(*args):
        raise error

    assert desktop_pdf.extract(worker, b"fixture", 5, desktop_pdf.time.monotonic() + 1) == ({"status": status}, 0)
