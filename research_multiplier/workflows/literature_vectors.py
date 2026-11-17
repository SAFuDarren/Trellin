"""Retrieve eligible research documents by model-scoped pgvector similarity."""

import json
from pathlib import Path


KINDS = ("source", "claim", "basis", "evidence", "failure")
MAX_VECTOR_FILE_BYTES = 4_194_304
MAX_DIMENSIONS = 2000
MAX_COMPONENT = float.fromhex("0x1.fffffep+127")


def read_document(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_VECTOR_FILE_BYTES + 1)
    if len(raw) > MAX_VECTOR_FILE_BYTES:
        raise ValueError("Vector input exceeds 4 MiB.")
    try:
        return json.loads(raw)
    except (ValueError, RecursionError):
        raise ValueError("Vector input needs a valid JSON document.") from None


def _profile(payload, field):
    if not isinstance(payload, dict) or set(payload) != {"model", "revision", "dimensions", field}:
        raise ValueError(f"Provide model, revision, dimensions and {field}.")
    if any(not isinstance(payload[key], str) or not payload[key].strip() or len(payload[key]) > 200
           for key in ("model", "revision")) or type(payload["dimensions"]) is not int \
            or not 1 <= payload["dimensions"] <= MAX_DIMENSIONS:
        raise ValueError("A profile requires nonempty model and revision up to 200 characters and 1–2000 dimensions.")
    return payload["model"], payload["revision"], payload["dimensions"]


def _vector(values, dimensions):
    if (not isinstance(values, list) or len(values) != dimensions
        or any(type(item) not in (int, float) or not -MAX_COMPONENT <= item <= MAX_COMPONENT for item in values)
        or not any(values)):
        raise ValueError("A vector needs the specified dimensions, finite float32 components and nonzero norm.")
    return values


def document(store, kind, document_id):
    rows = store.query("human", """
        SELECT kind, document_id AS id, claim_id, version_sha256, title, input, content_sha256
        FROM rm.literature_embedding_documents WHERE kind = %s AND document_id = %s
    """, kind, document_id)
    if not rows:
        raise ValueError("No current eligible document matches this kind and ID.")
    return rows[0]


def import_vectors(store, payload):
    model, revision, dimensions = _profile(payload, "documents")
    documents = payload["documents"]
    if not isinstance(documents, list) or not 1 <= len(documents) <= 100:
        raise ValueError("Import between one and one hundred document vectors per batch.")
    for item in documents:
        if not isinstance(item, dict) or set(item) != {"kind", "id", "content_sha256", "vector"}:
            raise ValueError("Each document needs kind, id, content_sha256 and vector.")
        _vector(item["vector"], dimensions)
    return store.query("human", "SELECT * FROM rm.import_literature_vectors(%s,%s,%s,%s)",
                       model, revision, dimensions, store.json(documents))[0]


def similar(store, payload, limit=20, kind=None):
    model, revision, dimensions = _profile(payload, "vector")
    values = _vector(payload["vector"], dimensions)
    return store.query("human", "SELECT * FROM rm.search_literature_vectors(%s,%s,%s,%s,%s,%s)",
                       model, revision, dimensions, store.json(values), limit, kind)


def profiles(store):
    return store.query("human", """
        SELECT id, model, model_revision AS revision, dimensions
        FROM rm.literature_vector_profiles ORDER BY model, model_revision, dimensions
    """)
