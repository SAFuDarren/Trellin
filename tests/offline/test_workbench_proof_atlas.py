from uuid import UUID

import pytest

from research_multiplier.workflows import proof_graph

pytestmark = pytest.mark.offline


def test_atlas_retains_searchable_titles_without_full_graph_records(monkeypatch):
    malformed, recent, older = (UUID(int=index) for index in range(1, 4))
    claims = [
        {"id": str(malformed), "title": "Sequences", "domain": "example", "status": "open"},
        {"id": str(recent), "title": "Finite sums", "domain": "example", "status": "open"},
        {"id": str(older), "title": "Products", "domain": "example", "status": "open"},
    ]

    class Store:
        def query(self, role, sql, *params):
            assert role == "human"
            if sql == proof_graph._GRAPHS:
                assert params == ([str(malformed), str(recent)],)
                return [{"id": malformed}, {"id": recent}]
            assert params == (100,)
            return claims

    def snapshot(row):
        if row["id"] == malformed:
            raise ValueError("Malformed graph")
        assert row["id"] == recent
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

    monkeypatch.setattr(proof_graph, "_snapshot", snapshot)

    result, = proof_graph.atlas(Store(), limit=2)["claims"]
    assert result["id"] == str(recent)
    assert result["nodes"] == [
        {"id": "obligation:example:2", "kind": "obligation", "status": "open", "title": "Proof obligation 2"},
        {"id": "item:library", "kind": "library", "status": "available", "title": "Nat.add_zero"},
        {"id": "lean:evidence", "kind": "evidence", "status": "awaiting_review", "title": "Lean goal",
         "citation": "Example.zero"},
    ]
