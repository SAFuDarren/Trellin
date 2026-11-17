"""Bounded, exact primal/dual certificates for rational LP and SDP instances."""

from dataclasses import dataclass
from fractions import Fraction
from copy import deepcopy
import re
from uuid import UUID, uuid4

from .domains import dot
from .serialization import json_text


RATIONAL = re.compile(r"[+-]?\d{1,36}(?:/\d{1,36}|\.\d{1,36})?\Z", re.ASCII)


def rational(value):
    if type(value) is int and abs(value) < 10**36:
        return Fraction(value)
    if isinstance(value, str) and RATIONAL.fullmatch(value):
        try:
            return Fraction(value)
        except ZeroDivisionError:
            pass
    raise ValueError("Use exact integers, fractions or short decimal strings for coefficients.")


def vector(values, count=None):
    if (not isinstance(values, list) or not 1 <= len(values) <= 64
            or (count is not None and len(values) != count)):
        raise ValueError("The rational vector dimensions do not match the instance.")
    return [rational(value) for value in values]


def symmetric(rows, count=None):
    if (not isinstance(rows, list) or not 1 <= len(rows) <= 24
            or (count is not None and len(rows) != count)):
        raise ValueError("Expected a symmetric matrix of the instance dimension, at most 24.")
    matrix = [vector(row, len(rows)) for row in rows]
    if any(matrix[i][j] != matrix[j][i] for i in range(len(rows)) for j in range(i)):
        raise ValueError("The matrix must be symmetric.")
    return matrix


def matrix_product(left, right):
    return sum(dot(x, y) for x, y in zip(left, right, strict=True))


def positive_semidefinite(matrix):
    """Exact symmetric elimination using positive diagonal pivots."""
    while matrix:
        if any(row[index] < 0 for index, row in enumerate(matrix)):
            return False
        pivot = next((i for i, row in enumerate(matrix) if row[i]), None)
        if pivot is None:
            return not any(value for row in matrix for value in row)
        diagonal = matrix[pivot][pivot]
        rest = [index for index in range(len(matrix)) if index != pivot]
        matrix = [
            [matrix[i][j] - matrix[i][pivot] * matrix[pivot][j] / diagonal for j in rest]
            for i in rest
        ]
    return True


def _lp_instance(spec):
    c = vector(spec["c"])
    b = vector(spec["b"])
    if not isinstance(spec["A"], list) or len(spec["A"]) != len(b):
        raise ValueError("A must have one row per constraint.")
    return c, [vector(row, len(c)) for row in spec["A"]], b


def _sdp_instance(spec):
    c = symmetric(spec["C"])
    b = vector(spec["b"])
    if not isinstance(spec["A"], list) or len(spec["A"]) != len(b):
        raise ValueError("A must have one symmetric matrix per equality.")
    return c, [symmetric(row, len(c)) for row in spec["A"]], b


def _lp_check(instance, witness):
    c, a, b = instance
    x = vector(witness["primal"], len(c))
    y = vector(witness["dual"], len(b))
    primal = all(z >= 0 for z in x) and all(dot(row, x) <= bound for row, bound in zip(a, b, strict=True))
    dual = all(z >= 0 for z in y) and all(dot(col, y) >= bound for col, bound in zip(zip(*a), c, strict=True))
    return primal, dual, dot(c, x), dot(b, y)


def _sdp_check(instance, witness):
    c, a, b = instance
    x = symmetric(witness["primal"], len(c))
    y = vector(witness["dual"], len(b))
    indices = range(len(c))
    slack = [[sum(y_i * matrix[i][j] for y_i, matrix in zip(y, a, strict=True)) - c[i][j]
              for j in indices] for i in indices]
    primal = positive_semidefinite(x) and all(
        matrix_product(matrix, x) == bound for matrix, bound in zip(a, b, strict=True)
    )
    return primal, positive_semidefinite(slack), matrix_product(c, x), dot(b, y)


@dataclass(frozen=True)
class Optimization:
    domain: str
    objective_key: str
    feasible: str

    def scope(self, spec):
        if not isinstance(spec, dict) or set(spec) != {"A", "b", self.objective_key, "target_value"}:
            raise ValueError(f"{self.domain} needs A, b, {self.objective_key} and target_value.")
        instance = _lp_instance if self.objective_key == "c" else _sdp_instance
        return instance(spec), rational(spec["target_value"])

    def _claim(self, spec, title, identifier):
        _, target = self.scope(spec)
        return {
            "format_version": 1, "claim_id": identifier, "kind": "researcher_claim", "origin": "human",
            "domain": self.domain, "title": title, "sensitivity": "public",
            "statement": f"The fixed optimization problem has a real maximum of {target}.",
            "formal_skeleton": {"optimization": self.domain, "target": str(target)},
            "small_case_spec": spec, "definition_ids": [],
            "hypotheses": [
                {"label": "H1", "statement": "The coefficients are exactly those of the specified instance.",
                 "formal": {key: spec[key] for key in ("A", "b", self.objective_key)}},
                {"label": "H2", "statement": self.feasible, "formal": {"convention": self.domain}},
            ],
            "conclusion": {
                "statement": f"Every feasible point has objective at most {target}, and one attains {target}.",
                "parameters": [], "constant_dependencies": {},
                "quantifiers": [{"type": "forall", "over": "feasible points"},
                                {"type": "exists", "over": "feasible points"}],
            },
        }

    def public_claim(self, spec, title):
        if not isinstance(title, str) or not title.strip():
            raise ValueError("Claim title must be nonempty text.")
        return self._claim(deepcopy(spec), title, str(uuid4()))

    def validate_claim(self, frozen):
        if not isinstance(frozen, dict) or not isinstance(frozen.get("claim_id"), str):
            raise ValueError("Expected a frozen optimization claim.")
        title, spec = frozen.get("title"), frozen.get("small_case_spec")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("Expected a named optimization claim.")
        if json_text(frozen) != json_text(self._claim(spec, title, str(UUID(frozen["claim_id"])))):
            raise ValueError("The submitted statement differs from the exact optimization contract.")
        return spec

    def build_packet(self, claim, *, ambiguities=None):
        self.validate_claim(claim)
        if ambiguities is None:
            ambiguities = {}
        if (not isinstance(ambiguities, dict) or ambiguities.keys() - {"goal", "H1", "H2"}
                or any(not isinstance(question, str) or not question.strip() for question in ambiguities.values())):
            raise ValueError("Use nonempty clarification questions for goal, H1 or H2.")
        items = [{"id": "goal", "kind": "goal", "natural": claim["conclusion"]["statement"],
                  "formal": claim["formal_skeleton"]}]
        items.extend({"id": row["label"], "kind": "hypothesis", "natural": row["statement"], "formal": row["formal"]}
                     for row in claim["hypotheses"])
        for row in items:
            question = ambiguities.get(row["id"])
            row["status"] = "needs_confirmation" if question else "stated"
            if question:
                row["question"] = question
        return {"format_version": 1, "claim_id": claim["claim_id"], "items": items}

    def validate_packet(self, body, frozen):
        if not isinstance(body, dict) or not isinstance(body.get("items"), list):
            raise ValueError("Expected a reviewed optimization statement.")
        questions = {row.get("id"): row["question"] for row in body["items"]
                     if isinstance(row, dict) and isinstance(row.get("id"), str) and "question" in row}
        if json_text(body) != json_text(self.build_packet(frozen, ambiguities=questions)):
            raise ValueError("The review packet differs from the frozen optimization problem.")

    def verify_witness(self, spec, witness):
        instance, target = self.scope(spec)
        if not isinstance(witness, dict) or set(witness) != {"primal", "dual"}:
            raise ValueError("Supply exactly the primal and dual rational witnesses.")
        check = _lp_check if self.objective_key == "c" else _sdp_check
        primal, dual, lower, upper = check(instance, witness)
        if not primal or not dual or lower != upper:
            raise ValueError("The witnesses must both be feasible and have matching exact objectives.")
        result = {"method": self.domain + "-exact-primal-dual", "objective": str(lower),
                  "target": str(target), "primal_feasible": True, "dual_feasible": True,
                  "objectives_match": True}
        if target != lower:
            result["evidence_kind"] = "counterexample"
        return result

    def verify_certificate(self, frozen, witness):
        return self.verify_witness(self.validate_claim(frozen), witness)

    def supplied(self, spec, witness):
        instance, target = self.scope(spec)
        if not isinstance(witness, dict) or set(witness) != {"primal", "dual"}:
            raise ValueError("Supply exactly the primal and dual rational witnesses.")
        objective = instance[0]
        suggested_value = (dot(objective, vector(witness["primal"], len(objective)))
                           if self.objective_key == "c"
                           else matrix_product(objective, symmetric(witness["primal"], len(objective))))
        result = {"proof_markdown": f"The supplied primal and dual witnesses purport to show optimum {suggested_value}.",
                  "basis_items": ["H1", "H2"], "certificate": witness}
        if suggested_value != target:
            result["evidence_kind"] = "counterexample"
        return result

    @staticmethod
    def candidate(_spec):
        return {"failure_reason": "Supply a candidate with --certificate and an exact primal/dual witness file."}


LP = Optimization("linear-program", "c", "The variables are real and nonnegative, and satisfy Ax ≤ b. Maximize c·x.")
SDP = Optimization("semidefinite-program", "C",
                   "X is a real symmetric positive semidefinite matrix, with ⟨Aᵢ,X⟩ = bᵢ. Maximize ⟨C,X⟩.")
