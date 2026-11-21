import pytest

from research_multiplier.workflows import proof_graph

pytestmark = pytest.mark.offline


def test_atlas_retains_searchable_titles_without_full_graph_records(monkeypatch):
    claims = [
        {"id": "recent", "title": "Finite sums", "domain": "example", "status": "open"},
        {"id": "older", "title": "Products", "domain": "example", "status": "open"},
    ]
    monkeypatch.setattr(proof_graph, "claims", lambda _store: claims)

    def snapshot(_store, claim_id, *, references):
        assert claim_id == "recent"
        assert not references
        return {
            "nodes": [
                {"id": "obligation:example:2", "kind": "obligation", "status": "open",
                 "title": "Proof obligation 2", "formalization": "True"},
                {"id": "item:library", "kind": "library", "status": "available", "title": "Library entry",
                 "citation": "Nat.add_zero"},
                {"id": "lean:evidence", "kind": "evidence", "status": "awaiting_review", "title": "Lean goal",
                 "citation": "Example.zero"},
            ],
            "edges": [],
            "blockers": [],
        }

    monkeypatch.setattr(proof_graph, "snapshot", snapshot)

    result, = proof_graph.atlas(object(), limit=1)["claims"]
    assert result["id"] == "recent"
    assert result["nodes"] == [
        {"id": "obligation:example:2", "kind": "obligation", "status": "open", "title": "Proof obligation 2"},
        {"id": "item:library", "kind": "library", "status": "available", "title": "Nat.add_zero"},
        {"id": "lean:evidence", "kind": "evidence", "status": "awaiting_review", "title": "Lean goal",
         "citation": "Example.zero"},
    ]
