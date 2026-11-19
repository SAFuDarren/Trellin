# SPDX-License-Identifier: Apache-2.0

import json
import os
from uuid import uuid4

import pytest

from research_multiplier.storage import paths, project_archive
from research_multiplier.workflows import research_handoff


pytestmark = pytest.mark.offline


def test_export_preserves_exact_scope_and_treats_user_text_as_literal(tmp_path, monkeypatch):
    workspace = paths.initialize_workspace(tmp_path / "project")
    target = workspace / "exports" / "handoff"
    digest = "a" * 64
    hostile_text = "Hypothesis.&\n```\n</pre><script>launch()</script>"
    document = {"scope": "claims", "workspace_id": str(paths.workspace_id(workspace)), "claims": [{
        "graph": {"claim": {"title": "A local proof", "statement": "Example statement", "status": "proved"},
                  "nodes": [], "edges": [], "blockers": []},
        "reference": {"claim_id": str(uuid4())},
        "scope": {"hypotheses": [{"label": "h", "statement": hostile_text,
                                    "formal": {"lean": {"proposition": "Nat.zero = 0"}}}], "basis_reviewed": True,
                  "conclusion": "Distinct exact conclusion", "formal_target": {"lean": "True → True"},
                  "lean_proposition": None},
        "evidence": [], "notes": {"guidance": [{"text": hostile_text}], "understanding": []},
        "decisions": [], "decision_limit_reached": False, "next": {"message": "Read the recorded result."},
        "sources": [{"title": "Source", "url": "https://arxiv.org/abs/2401.01234v1"}],
    }]}
    monkeypatch.setattr(research_handoff, "capture", lambda *_args, **_kw: {"snapshot_sha256": digest, "snapshot": document})
    with paths.use_workspace(workspace, strict=True):
        with pytest.raises(ValueError, match="Preview again"):
            research_handoff.handoff(None, claims=None, out=target, confirm="stale")
        assert not target.exists()
        alias = target.with_name("unrelated-link")
        alias.symlink_to(target, target_is_directory=True)
        with pytest.raises(ValueError, match="symbolic link"):
            research_handoff.handoff(None, claims=None, out=alias, confirm=digest)
        assert alias.is_symlink() and not target.exists()
        result = research_handoff.handoff(None, claims=None, out=target, confirm=digest)
        with pytest.raises(FileExistsError):
            research_handoff.handoff(None, claims=None, out=target, confirm=digest)

    assert result["files"] == ["handoff.html", "handoff.md"]
    html, markdown = [(target / name).read_text() for name in result["files"]]
    for rendered in (html, markdown):
        assert digest in rendered and result["captured_at"] in rendered
        assert "True → True" in rendered and "Distinct exact conclusion" in rendered
        assert "Read the recorded result." in rendered and "https://arxiv.org/abs/2401.01234v1" in rendered
    assert "<script>launch()" not in html and "&lt;script&gt;launch()" in html
    assert "@media print" in html and "default-src 'none'" in html
    assert "**Exact formal statement**" in markdown
    assert "**h**" in markdown and "Nat.zero = 0" in markdown
    assert html.index("Example statement") < html.index("Snapshot SHA-256")
    assert "````text\n" + hostile_text + "\n````" in markdown


def test_latest_backup_skips_corrupt_and_foreign_archives(tmp_path):
    workspace = paths.initialize_workspace(tmp_path / "project")
    database_id = uuid4()
    paths.bind_database(workspace, database_id)
    files = {"database.dump": b"test-only dump", f"project/{paths.WORKSPACE_FILE}": b'{"format_version":1}',
             f"project/{paths.WORKSPACE_ID_FILE}": str(paths.workspace_id(workspace)).encode(),
             f"project/{paths.DATABASE_FILE}": str(database_id).encode()}

    def archive(name, timestamp, *, foreign=False, corrupt=False):
        root = workspace / "backups" / name
        (root / "project").mkdir(parents=True)
        for relative, content in files.items():
            (root / relative).write_bytes(content)
        manifest = root / "manifest.json"
        manifest.write_text(json.dumps({"format_version": 1, "database_id": str(uuid4() if foreign else database_id),
                                        "workspace_id": str(paths.workspace_id(workspace)), "schema_revision": "0077",
                                        "files": {name: project_archive._fingerprint(root / name) for name in files}}))
        os.utime(manifest, (timestamp, timestamp))
        if corrupt:
            (root / "database.dump").write_bytes(b"changed")
        return root

    expected = archive("older-verified", 1)
    archive("newer-corrupt", 2, corrupt=True)
    archive("foreign", 3, foreign=True)
    with paths.use_workspace(workspace, strict=True):
        latest = project_archive.latest()
    assert latest["archive"]["directory"] == str(expected)
    assert latest["unusable_archives"] == 1
