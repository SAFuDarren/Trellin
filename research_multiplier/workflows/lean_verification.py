"""Shared local verification entry points for callers supplying proof candidates.

The bridge underneath these operations preserves the versioned verifier sources
used by older evidence. Source adapters supply propositions and proof terms;
scope, execution, receipt admission, and retry identities stay here.
"""

from research_multiplier import composed_lean, final_lean_local, lean_source, local_lemmas
from research_multiplier.policy.lean_search_scope import load_local_search_scope
from research_multiplier.proof.lean_blueprint import _proof
from research_multiplier.proof import lean_proof_search
from research_multiplier.storage import paths
from research_multiplier.storage.artifacts import Artifacts

check_goal = final_lean_local.check
check_source = lean_source.check_source
derive_plan = local_lemmas.derive
list_lemmas = local_lemmas.list_lemmas


def bind_plan_proof(proof, resolve_node):
    """Resolve earlier named plan nodes to exact confirmed lemma references."""
    return _proof(proof, resolve_node)


class LocalLeanVerification:
    """Use a single confirmed project scope for a series of local proofs."""

    def __init__(self, store, statement_id, *, lean=None, mathlib_project=None, retry=False):
        statement, frozen, context = load_local_search_scope(store, statement_id, allow_restricted=True)
        self.store = store
        self.statement = statement
        self.context = context
        self.lean = lean
        self.mathlib_project = mathlib_project
        self.retry = retry
        self._frozen = frozen
        self._contract = None
        self._checked = composed_lean.select_runtime(store, statement, lean=lean, mathlib_project=mathlib_project)
        self._known = {str(row["id"]): row for row in local_lemmas._rows(store, statement["id"])}
        self.artifacts = Artifacts(paths.object_store(), workspace=False)

    @property
    def goal(self):
        return self.context["consequent"]

    @property
    def modules(self):
        return self.context["approved_modules"]

    def suggest_proof(self, proposition):
        return (lean_proof_search.candidate(proposition, self.context, self._known)
                or lean_proof_search.telescope_candidate(proposition, self.context, self._known))

    def validate_types(self, sources, commands):
        """Run type checks in the same reviewed runtime as the proof receipts."""
        runtime = self._checked.runtime
        return (runtime.mathlib or runtime.native).run(
            {**sources, "TheoryAudit.lean": runtime.audit}, commands,
        )

    def check_lemma(self, node, names, *, on_receipt=None):
        """Verify and store an intermediate lemma and any transactional metadata."""
        frozen = self._frozen
        subject = {"claim_id": str(self.statement["claim_id"]), "contract_sha256": frozen["contract_sha256"]}
        if self._contract is None:
            self._contract = self.artifacts.put(frozen["contract_bytes"], raw=True)
        row = local_lemmas._derive(
            self.store, self.artifacts, self.statement, subject, self._contract, self._known, node, names,
            checked_runtime=self._checked, retry=self.retry, on_receipt=on_receipt,
        )
        self._known[str(row["id"])] = row
        return row

    def check_goal(self, proof, output=None, *, on_receipt=None):
        """Verify the whole frozen goal through the established acceptance path."""
        return check_goal(
            self.statement["id"], {"format_version": 1, "proof": proof}, output or paths.runs() / "lean",
            self.lean, mathlib_project=self.mathlib_project, retry=self.retry, checked_runtime=self._checked,
            on_receipt=on_receipt,
        )
