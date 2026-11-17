"""Read and replay proof documents bound to the Lean Core theorem library."""

from pathlib import Path
from uuid import UUID

from . import basis, paths
from .artifacts import Artifacts
from .lean_runner import artifact_ref
from .postgres import Store
from .serialization import json_text


def archive_candidate(artifacts, ref):
    destination = Artifacts(paths.object_store(), workspace=False)
    try:
        destination.read(ref)
    except FileNotFoundError:
        destination.put_bytes(artifacts.read(ref))


def publish(evidence_id, run_directory=None):
    with Store() as store:
        rows = store.query("verifier", """
            SELECT e.artifact_uri AS uri, e.artifact_sha256 AS sha256, checked.statement_id
            FROM rm.evidence_foundation_status e
            JOIN rm.lean_check_status checked ON checked.id = e.lean_check_id AND checked.check_ready
            JOIN rm.claims c ON c.id = e.claim_id AND c.status = 'proved' AND c.decisive_evidence_id = e.id
            WHERE e.id = %s AND e.verifier_name = 'rm-lean-core-audit'
              AND e.review_status = 'approved' AND NOT e.needs_recheck
        """, evidence_id)
        if not rows:
            raise ValueError("Only the current promoted exact Lean Core receipt can be exported.")
        source = rows[0]
        directory = (Path(run_directory) if run_directory is not None
                     else paths.runs() / "lean" / str(source["statement_id"]) / "core")
        ref = {key: source[key] for key in ("uri", "sha256")}
        destination = Artifacts(paths.object_store(), workspace=False)
        try:
            destination.read(ref)
        except FileNotFoundError:
            try:
                archive_candidate(Artifacts(directory / "artifacts"), ref)
            except FileNotFoundError:
                raise ValueError("The verified proof document is missing. Supply its archived --run-directory.") from None
        return store.query("verifier", "SELECT * FROM rm.publish_lean_core_theorem(%s)", evidence_id)[0]


def catalog(limit):
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("Choose a theorem list limit between 1 and 200.")
    with Store() as store:
        return store.query("human", """
            SELECT id, logical_key, version_sha256, evidence_id, body
            FROM rm.current_lean_core_theorems ORDER BY logical_key LIMIT %s
        """, limit)


def antecedents(item):
    values = item.get("formal", {}).get("lean", {}).get("antecedents")
    if (not isinstance(values, list) or len(values) > 128
            or not all(isinstance(value, str) and value.strip() for value in values)):
        raise ValueError("The theorem export has invalid frozen antecedents.")
    return values


class VerifiedCoreTheorems:
    """Resolve only records authorized against the current destination packet."""

    def __init__(self, store, packet_id, claim_id, manifest=None):
        self.store = store
        self.packet_id = packet_id
        self.claim_id = str(claim_id)
        self.manifest = manifest
        self.archive = Artifacts(paths.object_store(), workspace=False)

    def admit(self, identifier):
        try:
            canonical = str(UUID(str(identifier)))
        except ValueError:
            raise ValueError("A theorem reference needs an approved version UUID.") from None
        if str(identifier) != canonical:
            raise ValueError("Use the exact UUID returned by the theorem library.")
        rows = self.store.query("verifier", "SELECT * FROM rm.admit_lean_core_theorem(%s,%s)",
                                self.packet_id, canonical)
        return rows[0]["body"]

    def document(self, item, expected_source):
        source = item["source"]
        if source["claim_id"] == self.claim_id:
            raise ValueError("A goal cannot be checked by citing one of its own receipts.")
        if source["environment"].get("external") != self.manifest:
            raise ValueError("The theorem must be replayed with its original reviewed Mathlib project and imports.")
        if (artifact_ref(expected_source.encode("utf-8")) != source["trusted_source"]
                or source["checker"] != {"name": "rm-lean-core-audit", "version": "1"}
                or source["environment"]["sha256"] != source["environment_sha256"]
                or source["environment"]["inputs"]["driver"] != source["checker_revision"]):
            raise ValueError("The promoted theorem scope or verifier provenance is inconsistent.")
        try:
            document = self.archive.read_json(source["candidate"])
        except FileNotFoundError:
            raise ValueError("The proof document is unavailable in this workspace. Export the original receipt first.") from None
        if (not isinstance(document, dict) or document.get("statement") != expected_source
                or not isinstance(document.get("submission"), dict)
                or artifact_ref(json_text(document["submission"]).encode("utf-8")) != source["submission"]):
            raise ValueError("The archived theorem differs from its exact reviewed source or checked submission.")
        pinned = set(source["basis_version_ids"])
        items = [row for row in basis.versions(self.store, "verifier", source["packet_id"])
                 if row["id"] in pinned]
        if {row["id"] for row in items} != pinned:
            raise ValueError("The archived theorem has a basis version that is no longer available.")
        return document["submission"], items, pinned
