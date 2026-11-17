"""Read and register certified intermediate results for a specific frozen packet."""


def _review_entry(row):
    return {
        "id": f"D:{row['id']}",
        "item_key": row["item_key"],
        "natural": row["natural"],
        "formal": row["formal"],
        "premise_item_ids": row["premise_item_ids"],
        "verification_attempt_id": str(row["verification_attempt_id"]),
        "verification_artifact": {"uri": row["verification_uri"], "sha256": row["verification_sha256"]},
        "proof_attempt_id": str(row["proof_attempt_id"]),
        "proof_artifact": {"uri": row["artifact_uri"], "sha256": row["artifact_sha256"]},
    }


def available(store, role, packet_id):
    rows = store.query(role, """
        SELECT * FROM rm.verified_derivations
        WHERE packet_id = %s ORDER BY item_key, id
    """, packet_id)
    return [_review_entry(row) for row in rows]


def register(store, packet_id, checked):
    result = checked["result"]["verification"]
    rows = store.query("verifier", """
        SELECT * FROM rm.register_flow_derivations(%s, %s, %s)
        ORDER BY item_key, id
    """, packet_id, checked["id"], store.json(result))
    return [_review_entry(row) for row in rows]


def model_entries(rows):
    keys = ("id", "item_key", "natural", "formal", "premise_item_ids")
    return [{key: row[key] for key in keys} for row in rows]


def validate_basis(requirements, basis):
    if not isinstance(basis, list) or not basis or any(not isinstance(item, str) for item in basis):
        raise ValueError("The proof must declare its basis_items as a nonempty list of item IDs.")
    if len(basis) != len(set(basis)):
        raise ValueError("The proof must list each basis item once.")
    root_items = requirements["formalization"]["items"]
    roots = {item["id"] for item in root_items if item["kind"] != "goal"}
    verified = {item["id"]: item for item in requirements.get("verified_derivations", [])}
    invalid = set(basis) - roots - verified.keys()
    if invalid:
        raise ValueError(f"The proof used an unapproved or circular basis item: {min(invalid)}.")
    explicit_premises = set(basis) & roots
    for item in set(basis) & verified.keys():
        explicit_premises.update(verified[item]["premise_item_ids"])
    # The exact flow/cut checker needs the network, feasibility rules and value definition.
    required = set(requirements.get("required_basis_items", []))
    if not required <= explicit_premises:
        raise ValueError("The proof's declared basis omits a required premise.")
