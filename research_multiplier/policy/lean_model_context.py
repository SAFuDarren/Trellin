"""Project Lean search state onto the fields authorized for public model use."""

from dataclasses import dataclass

from ..proof.lean_decomposition import match_key
from ..proof.lean_proof_search import canonical
from research_multiplier.protocol.serialization import fingerprint


_ACTIONS = {"blocked", "lemma", "proof", "reduction", "refutation"}
_STATES = {
    "awaiting_evidence_review", "blocked", "budget_blocked", "duplicate_candidate", "lemma_verified",
    "proof_search_blocked", "proposal_failed", "proposal_unadmitted", "rejected", "route_checked",
}
_ROUTE_STATES = {"awaiting_obligations", "ready", "goal_checked", "refuted_obligation", "closure_rejected"}
_METHODS = {"advance_goal", "alternative_route", "key_lemma", "refutation"}
_PROGRAM_FEEDBACK = {
    "Return exactly one supported action using only listed IDs and no extra premises.",
    "The current method stalled. Try another route from the same confirmed basis.",
    "Try another checked route before stopping.",
    "This exact candidate was already tried. Use a different method.",
    "Use distinct intermediate obligations without restating the goal.",
    "This route already exists. Try another intermediate statement.",
    "Use a checked route or establish an existing obligation.",
    "The implication is checked. Establish its obligations from the confirmed scope.",
    "Leave this as an open proof obligation. Try a different derivation from the confirmed basis.",
    "The native checker rejected that proof. Recheck the binder types, antecedents and references.",
    "The lemma is checked. The open proof duties have not changed.",
}
_COUNTERS = {
    "verified_obligations", "total_obligations", "pending_obligations", "untried_obligations",
    "stalled_attempts", "refuted_obligations", "model_rounds", "execution_ms", "cached_input_tokens",
    "input_tokens", "output_tokens",
}


def _allowed(value, options):
    return isinstance(value, str) and value in options


@dataclass(frozen=True)
class PublicSearchView:
    """Allow exact current proof text and bounded machine-generated metadata."""

    scope: dict
    lemmas: dict
    routes: dict
    route_keys: frozenset

    @classmethod
    def load(cls, store, statement, scope, known, routes):
        rows = store.query("verifier", "SELECT * FROM rm.public_lean_model_lemmas(%s)", statement["id"])
        allowed = {str(row["id"]) for row in rows}
        lemmas = {identifier: row for identifier, row in known.items()
                  if identifier in allowed and row["status"] == "verified"}
        admitted = {row["implication_id"]: row for row in routes if row["implication_id"] in lemmas}
        keys = frozenset(fingerprint(sorted(key(obligation["proposition"]) for obligation in route["obligations"]))
                         for route in admitted.values() for key in (canonical, match_key))
        return cls(scope, lemmas, admitted, keys)

    def _focus(self, row):
        method = row.get("method")
        if not _allowed(method, _METHODS):
            return None
        if method != "key_lemma":
            return {"method": method}
        identifier = row.get("implication_id")
        route = self.routes.get(identifier) if isinstance(identifier, str) else None
        proposition = row.get("proposition")
        if route and any(item["proposition"] == proposition for item in route["obligations"]):
            return {"method": method, "implication_id": identifier, "proposition": proposition}
        return None

    def _history(self, row):
        projected = {key: row[key] for key, values in (
            ("action", _ACTIONS), ("status", _STATES), ("obligation_progress", {"unchanged", "verified"}),
            ("lemma_id", self.lemmas), ("reduction_id", self.routes), ("feedback", _PROGRAM_FEEDBACK),
        ) if _allowed(row.get(key), values)}
        focus = row.get("focus")
        if isinstance(focus, dict) and (selected := self._focus(focus)) is not None:
            projected["focus"] = selected
        return projected or None

    def _local(self, row):
        identifier = row.get("id")
        if not _allowed(identifier, self.lemmas):
            return None
        return {"id": identifier, "proposition": self.lemmas[identifier]["proposition"],
                "antecedents": self.scope["antecedents"]}

    def _route(self, row, section):
        identifier = row.get("implication_id")
        if not _allowed(identifier, self.routes):
            return None
        route = self.routes[identifier]
        status = row.get("status")
        if section == "route_failures":
            if not _allowed(status, {"closure_rejected", "route_refuted"}):
                return None
            fields = {"implication_id": identifier, "status": status}
            if _allowed(row.get("lemma_id"), self.lemmas):
                fields["lemma_id"] = row["lemma_id"]
            return fields
        if not _allowed(status, _ROUTE_STATES):
            return None
        fields = {"implication_id": identifier, "status": status}
        if section == "route_comparison":
            fields.update({key: row[key] for key in _COUNTERS
                           if type(row.get(key)) is int and row[key] >= 0})
            return fields
        obligations = row.get("obligations")
        if not isinstance(obligations, list) or len(obligations) != len(route["obligations"]):
            return None
        fields["obligations"] = []
        for supplied, checked in zip(obligations, route["obligations"]):
            proposition = checked["proposition"]
            if not isinstance(supplied, dict) or supplied.get("proposition") != proposition:
                return None
            identifier = supplied.get("lemma_id")
            permitted = self.lemmas.get(identifier) if isinstance(identifier, str) else None
            fields["obligations"].append({
                "proposition": proposition,
                "lemma_id": identifier if permitted and permitted["proposition"] == proposition else None,
            })
        return fields

    def project(self, request):
        projected = dict(self.scope)
        if type(request.get("round")) is int and 1 <= request["round"] <= 8:
            projected["round"] = request["round"]
        if isinstance(request.get("search_focus"), dict):
            focus = self._focus(request["search_focus"])
            if focus is not None:
                projected["search_focus"] = focus
        if isinstance(request.get("checked_route_keys"), list):
            projected["checked_route_keys"] = [key for key in request["checked_route_keys"]
                                               if _allowed(key, self.route_keys)]
        for key, select in (("history", self._history), ("checked_local_lemmas", self._local),
                            ("open_routes", self._route), ("route_comparison", self._route),
                            ("route_failures", self._route)):
            rows = request.get(key)
            if not isinstance(rows, list):
                continue
            projected[key] = []
            for row in rows:
                if isinstance(row, dict):
                    item = select(row) if key in {"history", "checked_local_lemmas"} else select(row, key)
                    if item is not None:
                        projected[key].append(item)
        return projected

    def require(self, request):
        if not isinstance(request, dict) or self.project(request) != request:
            raise ValueError("The stored Lean request includes context no longer authorized for public model use.")
