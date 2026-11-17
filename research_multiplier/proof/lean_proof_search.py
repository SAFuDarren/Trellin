"""Propose bounded proof terms using only the currently confirmed Lean scope.

This search operates on a small structural fragment. Its answers are only
candidates. The normal verifier elaborates and checks the exact frozen scope.
"""

MAX_GOAL_CHARS = 4096
MAX_FACTS = 64
SEARCH_STEPS = 96
MAX_DEPTH = 8

_OPERATORS = {"→": 0, "∨": 1, "∧": 2, "=": 3}
_DELIMITERS = {"(": ")", "[": "]", "{": "}"}


def _top_level(text):
    stack = []
    for index, character in enumerate(text):
        if character in _DELIMITERS:
            stack.append(_DELIMITERS[character])
        elif stack and character == stack[-1]:
            stack.pop()
            if not stack:
                yield index, character
        elif not stack:
            yield index, character


def canonical(text):
    """A stable comparison key, not a test for Lean definitional equality."""
    text = " ".join(text.split())
    for _ in range(MAX_DEPTH):
        if not text.startswith("(") or not text.endswith(")"):
            break
        closing = next((index for index, char in _top_level(text) if char == ")"), None)
        if closing != len(text) - 1:
            break
        text = text[1:-1].strip()
    return text


def _operator(text):
    # Lean Unicode notation only. Refuse tokens whose binders, quoting or
    # lexical rules would change the grouping this limited planner assumes.
    if text.startswith(("∀", "∃", "fun ", "let ", "if ", "match ")) or any(
            symbol in text for symbol in ('"', '«', '--', '/-')):
        return None
    selected = None
    for index, symbol in _top_level(text):
        if symbol == "=":
            before = text[index - 1:index]
            after = text[index + 1:index + 2]
            if before in {"<", ">", "!", ":", "="} or after in {"=", ">"}:
                continue
        if symbol in _OPERATORS and (selected is None or _OPERATORS[symbol] < _OPERATORS[selected[0]]):
            selected = (symbol, canonical(text[:index]), canonical(text[index + 1:]))
    return selected


def _without_ascription(text):
    colon = next((index for index, char in _top_level(text) if char == ":"), None)
    return text if colon is None else canonical(text[:colon])


def _apply(term, arguments):
    return {"apply": [term, *arguments]} if arguments else term


class _Search:
    def __init__(self, context, local):
        self.remaining = SEARCH_STEPS
        hypotheses = context["antecedents"]
        parameter_count = len(context.get("parameters", ()))
        self.scope_count = parameter_count + len(hypotheses)
        self.hypotheses = [(canonical(premise), {"use": index})
                           for index, premise in enumerate(hypotheses, parameter_count)]
        arguments = [{"use": index} for index in range(self.scope_count)]
        self.theorems = []
        for item in context["approved_theorems"][:MAX_FACTS]:
            conditions = () if item.get("application") == "native" else tuple(map(canonical, item["antecedents"]))
            self.theorems.append((canonical(item["proposition"]), {"ref": item["id"]}, conditions))
        for identifier, row in list(local.items())[-MAX_FACTS:]:
            if row["status"] == "verified":
                proof = _apply({"ref": identifier}, arguments)
                self.theorems.append((canonical(row["proposition"]), proof, ()))

    def proof(self, target):
        if len(target) > MAX_GOAL_CHARS or self.scope_count > 23:
            return None
        body = self._solve(canonical(target), self.hypotheses, frozenset(), MAX_DEPTH, self.scope_count)
        if body is None or not self.scope_count:
            return body
        return {"intro": {"count": self.scope_count, "body": body}}

    def _solve(self, goal, hypotheses, active, depth, introduced):
        if not self.remaining or not depth or not goal:
            return None
        # Introductions can provide a useful premise not available to the
        # parent goal. Include the exact local context in the cycle key.
        state = goal, introduced, tuple(proposition for proposition, _ in hypotheses)
        if state in active:
            return None
        self.remaining -= 1
        active = active | {state}
        facts = [(prop, term, ()) for prop, term in hypotheses] + self.theorems
        for proposition, term, conditions in facts:
            if proposition == goal and not conditions:
                return term
        if goal == "True":
            return {"trivial": True}
        connective = _operator(goal)
        if connective:
            op, left, right = connective
            if op == "=" and left and _without_ascription(left) == _without_ascription(right):
                return {"rfl": True}
            if op == "→":
                opened = [*hypotheses, (left, {"use": introduced})]
                result = self._solve(right, opened, active, depth - 1, introduced + 1)
                if result is not None:
                    return {"intro": {"count": 1, "body": result}}
            if op == "∧":
                first = self._solve(left, hypotheses, active, depth - 1, introduced)
                second = self._solve(right, hypotheses, active, depth - 1, introduced) if first is not None else None
                if second is not None:
                    return {"and": [first, second]}
            if op == "∨":
                for tag, proposition in (("left", left), ("right", right)):
                    result = self._solve(proposition, hypotheses, active, depth - 1, introduced)
                    if result is not None:
                        return {tag: result}
        for proposition, term, conditions in facts:
            while proposition != goal and len(conditions) < MAX_DEPTH:
                shape = _operator(proposition)
                if shape is None or shape[0] != "→":
                    break
                _, premise, proposition = shape
                conditions = (*conditions, premise)
            if proposition != goal or not conditions:
                continue
            arguments = []
            for premise in conditions:
                argument = self._solve(premise, hypotheses, active, depth - 1, introduced)
                if argument is None:
                    break
                arguments.append(argument)
            if len(arguments) == len(conditions):
                return _apply(term, arguments)
        return None


def candidate(target, context, local):
    """Return one small structural proof candidate, or no proposal."""
    return _Search(context, local).proof(target)


def _apply_typed_prefix(proposition, term, variables):
    from .lean_decomposition import forall, rename

    names = {}
    arguments = []
    while (split := forall(proposition)) is not None:
        binder, proposition = split
        for group, domain in binder.groups:
            domain = canonical(rename(domain, names))
            for name in group:
                if name in names or len(arguments) >= len(variables):
                    return None
                variable, kind, index = variables[len(arguments)]
                if domain != kind:
                    return None
                names[name] = variable
                arguments.append({"use": index})
    if not arguments or any(mark in proposition for mark in (
            "∀", "∃", "λ", "fun ", "let ", "match ", "=>", '"', "«", "--", "/-")):
        return None
    return canonical(rename(proposition, names)), _apply(term, arguments), ()


def _uses_instantiated_term(proof, terms):
    if isinstance(proof, dict):
        return proof in terms or any(_uses_instantiated_term(item, terms) for item in proof.values())
    if isinstance(proof, list):
        return any(_uses_instantiated_term(item, terms) for item in proof)
    return False


def telescope_candidate(target, context, local, *, require_quantified_fact=False):
    """Use approved facts within an explicitly typed telescope, without general unification."""
    from .lean_decomposition import _peel, introduce, match_key

    parameters = context.get("parameters", ())
    scope_count = len(parameters) + len(context["antecedents"])
    if len(target) > MAX_GOAL_CHARS or scope_count > 23:
        return None
    target = canonical(target)
    binders, body = _peel(target)
    if not binders and not parameters:
        return None
    search = _Search(context, local)
    facts = list(search.hypotheses)
    available = [(proposition, term, ()) for proposition, term in facts] + search.theorems
    if binders and binders[0].groups:
        key = match_key(target)
        for proposition, term, conditions in available:
            if not conditions and proposition.startswith("∀") and match_key(proposition) == key:
                return introduce(scope_count, term)

    variables = [(parameter["name"], canonical(parameter["type"]), index)
                 for index, parameter in enumerate(parameters)]
    names = {name for name, _, _ in variables}
    introduced = search.scope_count
    for binder in binders:
        if binder.premise is not None:
            facts.append((binder.premise, {"use": introduced}))
            introduced += 1
            continue
        for group, domain in binder.groups:
            domain = canonical(domain)
            for name in group:
                if name in names:
                    return None
                names.add(name)
                variables.append((name, domain, introduced))
                introduced += 1
    instantiated_terms = []
    if variables:
        available.extend((proposition, term, ()) for proposition, term in facts[len(search.hypotheses):])
        for proposition, term, conditions in available:
            if not conditions and proposition.startswith("∀"):
                instantiated = _apply_typed_prefix(proposition, term, variables)
                if instantiated is not None:
                    search.theorems.append(instantiated)
                    instantiated_terms.append(instantiated[1])
    if require_quantified_fact and not instantiated_terms:
        return None
    result = search._solve(body, facts, frozenset(), MAX_DEPTH, introduced)
    if require_quantified_fact and not _uses_instantiated_term(result, instantiated_terms):
        return None
    return introduce(introduced, result) if result is not None else None


def conjunction(target, premise_count):
    """Form a route for a structural conjunction, with its checked obligation order."""
    if len(target) > MAX_GOAL_CHARS or premise_count > 22:
        return None
    syntactic = _operator(canonical(target))
    if syntactic is None or syntactic[0] != "∧":
        return None
    _, left, right = syntactic
    obligations = list(dict.fromkeys((left, right)))
    if not all(obligations):
        return None
    return {
        "format_version": 1,
        "obligations": obligations,
        "proof": {"intro": {"count": premise_count + len(obligations), "body": {
            "and": [{"use": premise_count}, {"use": premise_count + len(obligations) - 1}]
        }}},
    }
