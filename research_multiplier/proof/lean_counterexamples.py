"""Suggest finite witnesses for a small fragment of a frozen Lean scope.

Evaluating this fragment only selects a candidate. The normal scoped Lean
verifier must prove the negation of the complete original proposition.
"""

from itertools import islice, product
from operator import add, eq, ge, gt, le, lt, mul, ne
import re

from .lean_proof_search import _top_level, canonical


MAX_TEXT = 600
MAX_DEPTH = 12
MAX_BINDERS = 6
MAX_TRIALS = 4096
MAX_PREMISES = 8
MAX_NAT_WITNESS = 16
MAX_VALUE = 1_000_000
_IDENTIFIER = r"[a-zA-Z_][a-zA-Z_0-9]*"
_NUMERAL = re.compile(r"[0-9]{1,5}")
_BINDER = re.compile(rf"(?P<names>{_IDENTIFIER}(?:\s+{_IDENTIFIER})*)\s*:\s*(?P<sort>.+)")
_GROUP = re.compile(r"\s*\(([^()]*)\)")
_PROPOSITIONS = {"True": True, "_root_.True": True, "False": False, "_root_.False": False}
_BOOLS = {name: value for literal, value in (("true", True), ("false", False))
          for name in (literal, "Bool." + literal, "_root_.Bool." + literal)}
_CONNECTIVES = ("↔", "→", "∨", "∧")
_COMPARISONS = {"=": eq, "≠": ne, "≤": le, "≥": ge, "<": lt, ">": gt}
_ARITHMETIC = {
    "+": add, "-": lambda left, right: max(0, left - right), "*": mul,
    "/": lambda left, right: left // right if right else 0,
    "%": lambda left, right: left % right if right else left,
}
_BOOL = (False, True)
_FINITE_ARITIES = {"Bool → Prop": 1, "Bool → Bool → Prop": 2}
_SORTS = {"Nat": range(MAX_NAT_WITNESS + 1), "Bool": _BOOL,
          **{sort: tuple(product(_BOOL, repeat=2**arity)) for sort, arity in _FINITE_ARITIES.items()}}
_LITERAL_NODES = {"Nat": "nat_literal", "Bool": "bool_literal",
                  "Bool → Prop": "bool_predicate_literal", "Bool → Bool → Prop": "bool_relation_literal"}


class UnsupportedFormula(ValueError):
    """The finite witness evaluator did not recognize this formula."""


def _split(text, operators, *, last=False):
    choices = {operator: 0 if last else rank for rank, operator in enumerate(operators)}
    found = None
    for index, operator in _top_level(text):
        if operator not in choices:
            continue
        if found is None or choices[operator] < choices[found[0]] or (last and choices[operator] == choices[found[0]]):
            found = (operator, index)
    if found is None:
        return None
    operator, index = found
    return operator, text[:index], text[index + 1:]


def _parts(text, variables, remaining=MAX_DEPTH):
    if not remaining or not text or len(text) > MAX_TEXT:
        raise UnsupportedFormula()
    text = canonical(text)
    if text.startswith(("∀", "∃")):
        kind, binders, body = _quantifier(text, variables)
        if any(sort != "Bool" for sort in binders.values()):
            raise UnsupportedFormula()
        return ("quantified_bool", kind, tuple(binders),
                _parts(body, {**variables, **binders}, remaining - 1))
    connective = _split(text, _CONNECTIVES)
    if connective:
        operator, left, right = connective
        return (operator, _parts(left, variables, remaining - 1), _parts(right, variables, remaining - 1))
    if text.startswith("¬"):
        return ("not", _parts(text[1:], variables, remaining - 1))
    comparison = _split(text, tuple(_COMPARISONS))
    if comparison:
        operator, left, right = comparison
        return (operator, _parts(left, variables, remaining - 1), _parts(right, variables, remaining - 1))
    # + and - have the same precedence and associate to the left.
    arithmetic = (_split(text, "+-", last=True) or _split(text, "*/%", last=True)
                  or _split(text, "^"))
    if arithmetic:
        operator, left, right = arithmetic
        return (operator, _parts(left, variables, remaining - 1), _parts(right, variables, remaining - 1))
    ascription = _split(text, ":")
    if ascription:
        _, left, sort = ascription
        sort = sort.strip().removeprefix("_root_.")
        if sort in _SORTS:
            return ("ascribe", sort, _parts(left, variables, remaining - 1))
        raise UnsupportedFormula()
    if text in _PROPOSITIONS:
        return ("literal", "Prop", _PROPOSITIONS[text])
    if text in _BOOLS:
        return ("literal", "Bool", _BOOLS[text])
    if _NUMERAL.fullmatch(text):
        return ("literal", "Nat", int(text))
    if text in variables:
        return ("variable", text)
    application = text.split()
    if not application:
        raise UnsupportedFormula()
    head, *arguments = application
    arity = _FINITE_ARITIES.get(variables.get(head))
    if arity is not None and len(arguments) == arity:
        return ("relation", head, tuple(_parts(item, variables, remaining - 1) for item in arguments))
    raise UnsupportedFormula()


def _propositional(operator, left, right):
    if operator == "→":
        return not left or right
    if operator == "↔":
        return left == right
    if operator == "∨":
        return left or right
    return left and right


def _evaluate(node, binding):
    operator, *arguments = node
    if operator == "literal":
        return tuple(arguments)
    if operator == "variable":
        return binding[arguments[0]]
    if operator == "not":
        return "Prop", not _truth(arguments[0], binding)
    if operator == "quantified_bool":
        kind, names, body = arguments
        results = (_truth(body, {**binding, **dict(zip(names, (("Bool", value) for value in values)))})
                   for values in product(_BOOL, repeat=len(names)))
        return "Prop", (all(results) if kind == "∀" else any(results))
    if operator == "relation":
        head, terms = arguments
        relation_sort, table = binding[head]
        values = [_evaluate(term, binding) for term in terms]
        if len(values) != _FINITE_ARITIES[relation_sort] or any(sort != "Bool" for sort, _ in values):
            raise UnsupportedFormula()
        index = sum(int(value) * 2**(len(values) - position - 1)
                    for position, (_, value) in enumerate(values))
        return "Prop", table[index]
    if operator == "ascribe":
        sort, value = _evaluate(arguments[1], binding)
        if sort != arguments[0]:
            raise UnsupportedFormula()
        return sort, value

    left, right = arguments
    left_sort, left_value = _evaluate(left, binding)
    right_sort, right_value = _evaluate(right, binding)
    if left_sort != right_sort:
        raise UnsupportedFormula()
    if operator in _CONNECTIVES and left_sort == "Prop":
        return "Prop", _propositional(operator, left_value, right_value)
    if operator in _COMPARISONS and (left_sort == "Nat" or left_sort == "Bool" and operator in {"=", "≠"}):
        return "Prop", _COMPARISONS[operator](left_value, right_value)
    if left_sort == "Nat":
        if operator in _ARITHMETIC:
            value = _ARITHMETIC[operator](left_value, right_value)
            if value <= MAX_VALUE:
                return "Nat", value
        if operator == "^" and right_value <= 6:
            value = left_value ** right_value
            if value <= MAX_VALUE:
                return "Nat", value
    raise UnsupportedFormula()


def _truth(node, binding):
    sort, value = _evaluate(node, binding)
    if sort != "Prop":
        raise UnsupportedFormula()
    return value


def _quantifier(text, known):
    split = _split(text[1:], ",")
    if not split:
        raise UnsupportedFormula()
    _, declaration, consequence = split
    groups = []
    remaining = declaration.strip()
    if remaining.startswith("("):
        while remaining:
            group = _GROUP.match(remaining)
            if not group:
                raise UnsupportedFormula()
            groups.append(group[1])
            remaining = remaining[group.end():].strip()
    else:
        groups.append(remaining)
    binders = {}
    for group in groups:
        binder = _BINDER.fullmatch(group.strip())
        if not binder:
            raise UnsupportedFormula()
        sort = " → ".join(part.strip().removeprefix("_root_.") for part in binder["sort"].split("→"))
        if sort not in _SORTS:
            raise UnsupportedFormula()
        for name in binder["names"].split():
            if (name in known or name in binders or name in _PROPOSITIONS or name in _BOOLS
                    or len(binders) + len(known) >= MAX_BINDERS):
                raise UnsupportedFormula()
            binders[name] = sort
    return text[0], binders, canonical(consequence)


def _telescope(text):
    variables = {}
    steps = []
    text = canonical(text)
    guard_count = 0
    while True:
        if text.startswith("∀"):
            _, binders, text = _quantifier(text, variables)
            variables.update(binders)
            steps.extend(("binder", name) for name in binders)
            continue
        connective = _split(text, _CONNECTIVES)
        if not connective or connective[0] != "→":
            return variables, steps, _parts(text, variables)
        if guard_count >= MAX_PREMISES:
            raise UnsupportedFormula()
        _, premise, body = connective
        steps.append(("guard", _parts(premise, variables)))
        guard_count += 1
        text = canonical(body)


def _proof(binding, hypotheses, steps):
    if not binding:
        return {"decide": True}
    arguments = [{"decide": True} for _ in hypotheses]
    for kind, argument in steps:
        if kind == "guard":
            arguments.append({"decide": True})
        else:
            sort, value = binding[argument]
            arguments.append({_LITERAL_NODES[sort]: list(value) if isinstance(value, tuple) else value})
    return {"intro": {"count": 1, "body": {"decide_false": {"apply": [{"use": 0}, *arguments]}}}}


def candidate(context):
    """Return one candidate for exact refutation, or leave an unrecognized goal open."""
    hypotheses = context["antecedents"]
    target = context["consequent"]
    if len(hypotheses) > MAX_PREMISES or len(target) > MAX_TEXT:
        return None
    try:
        if not all(_truth(_parts(premise, {}), {}) for premise in hypotheses):
            return None
        variables, steps, result = _telescope(target)
        guards = [item for kind, item in steps if kind == "guard"]
        values_to_try = islice(product(*(_SORTS[sort] for sort in variables.values())), MAX_TRIALS)
        for values in values_to_try:
            binding = {name: (sort, value) for (name, sort), value in zip(variables.items(), values)}
            if any(not _truth(guard, binding) for guard in guards) or _truth(result, binding):
                continue
            return {"proof": _proof(binding, hypotheses, steps),
                    "witness": {"kind": "finite_assignment" if binding else "closed_proposition",
                                "assignments": [{"variable": name, "type": sort,
                                                 "value": list(value) if isinstance(value, tuple) else value}
                                                for name, (sort, value) in binding.items()],
                                "frozen_hypotheses": len(hypotheses), "guard_count": len(guards)}}
    except UnsupportedFormula:
        return None
    return None



def boolean_proof(context):
    """Suggest a proof only after enumerating an entire supported Boolean scope."""
    hypotheses = context["antecedents"]
    target = context["consequent"]
    if len(hypotheses) > MAX_PREMISES or len(target) > MAX_TEXT:
        return None
    try:
        variables, steps, result = _telescope(target)
        if not variables or set(variables.values()) != {"Bool"} or 2 ** len(variables) > MAX_TRIALS:
            return None
        premises = [_parts(premise, {}) for premise in hypotheses]
        guards = [item for kind, item in steps if kind == "guard"]
        success = all(_truth(premise, {}) for premise in premises)
        for values in product(_BOOL, repeat=len(variables)):
            binding = {name: ("Bool", value) for name, value in zip(variables, values)}
            if success and all(_truth(guard, binding) for guard in guards) and not _truth(result, binding):
                return None
    except UnsupportedFormula:
        return None
    return {"proof": {"decide": True}, "assignments_checked": 2 ** len(variables)}
