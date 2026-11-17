"""Versioned review packets and their admission to the proof workflow."""

from research_multiplier.workflows import derivations
from research_multiplier.domain_packages import production_domains
from research_multiplier.policy.contracts import validate
from research_multiplier.storage.postgres import Store


def draft(spec, title, *, ambiguities=None, domain=None):
    handler = production_domains.get(domain or production_domains.DEFAULT_DOMAIN)
    claim = handler.public_claim(spec, title)
    return propose_packet(claim, handler.build_packet(claim, ambiguities=ambiguities))


def propose_packet(claim, packet):
    """Store a structured statement and review packet before researcher confirmation."""
    validate("claim", "ClaimContract", claim)
    with Store() as store:
        return store.query("human", "SELECT * FROM rm.propose_formalization(%s, %s)",
                           store.json(packet), store.json(claim))[0]


def read(store, column, identifier):
    condition = {
        "id": "id = %s",
        "claim_id": "claim_id = %s AND NOT superseded",
    }[column]
    rows = store.query("verifier", f"SELECT * FROM rm.formalization_status WHERE {condition}", identifier)
    return rows[0] if rows else None


def show(packet_id):
    with Store() as store:
        rows = store.query("human", "SELECT * FROM rm.formalization_status WHERE id = %s", packet_id)
        if not rows:
            raise ValueError("No formalization packet matches this ID.")
        status = rows[0]
        status["clarifications"] = clarifications(store, "human", status["id"])
        status["verified_derivations"] = derivations.available(store, "human", status["id"])
        return status


def confirm(packet_id, version, checksum, resolved, actor, reason):
    with Store() as store:
        return store.query("human", "SELECT * FROM rm.confirm_formalization(%s,%s,%s,%s::text[],%s,%s)",
                           packet_id, version, checksum, resolved, actor, reason)[0]


def clarifications(store, role, packet_id):
    return store.query(role, """
        SELECT * FROM rm.formalization_clarification_status WHERE packet_id = %s
        ORDER BY requested_at, id
    """, packet_id)


def request(store, packet_id, item_id, question, impact):
    return store.query("scheduler", "SELECT rm.request_clarification(%s,%s,%s,%s) AS id",
                       packet_id, item_id, question, impact)[0]["id"]


def resolve(request_id, actor, reason):
    with Store() as store:
        return store.query("human", "SELECT rm.resolve_clarification(%s,%s,%s) AS id",
                           request_id, actor, reason)[0]["id"]


def proof_packet(status, frozen):
    if not status["proof_ready"]:
        raise ValueError("Formalization requires human confirmation or resolution before proof.")
    production_domains.get(frozen.get("domain")).validate_packet(status["packet"], frozen)
    items = [{key: item[key] for key in ("id", "kind", "natural", "formal")}
             for item in status["packet"]["items"]]
    packet = {"id": str(status["id"]), "version": status["version"],
              "packet_sha256": status["packet_sha256"], "items": items}
    if status.get("basis_packet_id"):
        packet["exploration_basis"] = {"id": str(status["basis_packet_id"]),
                                       "packet_sha256": status["basis_packet_sha256"]}
    return packet
