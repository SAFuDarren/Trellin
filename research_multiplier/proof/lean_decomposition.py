"""Propose checked conjunction reductions under an exact Lean telescope."""

from dataclasses import dataclass
import re

from .lean_proof_search import MAX_GOAL_CHARS, _operator, _top_level, canonical


MAX_TELESCOPE_BINDERS = 16
MAX_OBLIGATIONS = 8
_IDENTIFIERS = re.compile(r"(?<![.\w'])[^\W\d][\w']*(?![.\w'])")
_TOKENS = re.compile(r"[^\W\d][\w']*|\d+|[^\s]", re.UNICODE)


@dataclass(frozen=True)
class Binder:
    prefix: str
    count: int
    premise: str | None = None
    groups: tuple = ()


def _names(text):
    words = text.split()
    if words and all(word.rstrip("'").isidentifier() for word in words):
        return tuple(words)
    return ()


def _typed_group(text):
    colon = next((index for index, char in _top_level(text) if char == ":"), -1)
    if colon <= 0 or not text[colon + 1:].strip():
        return None
    names = _names(text[:colon])
    return (names, text[colon + 1:].strip()) if names else None


def forall(text):
    """Read an outer explicitly typed universal binder without interpreting Lean types."""
    if not text.startswith("∀") or any(mark in text for mark in ('"', '«', "--", "/-")):
        return None
    end = next((index for index, char in _top_level(text) if char == ","), -1)
    if end < 0 or not text[end + 1:].strip():
        return None
    head = text[1:end].strip()
    if not head.startswith("("):
        group = _typed_group(head)
        if not group:
            return None
        groups = [group]
    else:
        groups = []
        while head.startswith("("):
            closing = next((index for index, char in _top_level(head) if char == ")"), -1)
            if closing < 0:
                return None
            group = _typed_group(head[1:closing])
            if not group:
                return None
            groups.append(group)
            head = head[closing + 1:].strip()
        if head:
            return None
    count = sum(len(names) for names, _ in groups)
    if not 0 < count <= MAX_TELESCOPE_BINDERS:
        return None
    return Binder(text[:end + 1] + " ", count, groups=tuple(groups)), canonical(text[end + 1:])


def rename(text, names):
    return _IDENTIFIERS.sub(lambda token: names.get(token[0], token[0]), text)


def match_key(proposition):
    """Alpha-normalize explicitly typed leading quantifiers for candidate discovery."""
    text = canonical(proposition)
    names = {}
    types = []
    while (split := forall(text)) is not None:
        binder, text = split
        for group, domain in binder.groups:
            group_type = tuple(_TOKENS.findall(rename(domain, names)))
            for name in group:
                names[name] = f"rmAlpha{len(types)}"
                types.append(group_type)
    premises = []
    for _ in range(MAX_TELESCOPE_BINDERS):
        split = _operator(text)
        if split is None or split[0] != "→":
            break
        _, premise, text = split
        # The planner parenthesizes a lifted guard. Its grouping inside the
        # guard still matters, so retain the tokens under one fixed wrapper.
        premises.extend(("(", *_TOKENS.findall(rename(premise, names)), ")", "→"))
    return tuple(types), (*premises, *_TOKENS.findall(rename(text, names)))


def _peel(text):
    binders = []
    count = 0
    while count < MAX_TELESCOPE_BINDERS:
        if text.startswith("∀"):
            split = forall(text)
            if split is None:
                return (), text
            binder, text = split
        else:
            split = _operator(text)
            if split is None or split[0] != "→":
                break
            _, premise, text = split
            binder = Binder(f"({premise}) → ", 1, premise)
        count += binder.count
        binders.append(binder)
    if count > MAX_TELESCOPE_BINDERS:
        return (), text
    return tuple(binders), text



def conditional_route_premises(proposition, goal):
    """Find a checked candidate's extra arrows under matching typed parameters.

    This is a structural application test. The resulting obligations stay
    under the frozen goal's local context and still require Lean proofs.
    """
    def parameters(text):
        prefix = []
        names = []
        while (part := forall(text)) is not None:
            binder, text = part
            prefix.append(binder.prefix)
            names.extend(name for group, _ in binder.groups for name in group)
            if len(names) > MAX_TELESCOPE_BINDERS or len(set(names)) != len(names):
                return None
        return "".join(prefix), names, text

    source = parameters(canonical(proposition))
    target = parameters(canonical(goal))
    if source is None or target is None:
        return ()
    prefix, names, remaining = source
    target_prefix, target_names, local_goal = target
    if match_key(prefix + "True") != match_key(target_prefix + "True"):
        return ()
    target_key = match_key(goal)
    required = []
    while match_key(prefix + remaining) != target_key:
        shape = _operator(remaining)
        if shape is None or shape[0] != "→" or len(required) == MAX_OBLIGATIONS:
            return ()
        _, premise, remaining = shape
        required.append(premise)
    # The destination may already introduce propositions locally. Keep those
    # binders around candidate inputs rather than asking for stronger claims.
    target_guards, _ = _peel(local_goal)
    if any(binder.groups for binder in target_guards):
        return ()
    active = "".join(rename(binder.prefix, dict(zip(target_names, names))) for binder in target_guards)
    return tuple(dict.fromkeys(
        f"{prefix}{active}({premise})" if prefix or active else premise for premise in required
    ))


def _conjuncts(text, leaves, depth=0):
    split = _operator(text)
    if split is not None and split[0] == "∧":
        if depth >= MAX_OBLIGATIONS:
            raise ValueError("The conjunction is too deep to split.")
        return {"and": [_conjuncts(piece, leaves, depth + 1) for piece in split[1:]]}
    index = leaves.setdefault(text, len(leaves))
    if len(leaves) > MAX_OBLIGATIONS:
        raise ValueError("The conjunction has too many distinct obligations.")
    return index


def _apply_leaves(shape, obligation_start, telescope):
    if isinstance(shape, int):
        proof = {"use": obligation_start + shape}
        if telescope:
            return {"apply": [proof, *telescope]}
        return proof
    return {"and": [_apply_leaves(piece, obligation_start, telescope) for piece in shape["and"]]}


def introduce(count, proof):
    """Construct nested intros where the structured term bounds a single intro to 24."""
    while count > 24:
        proof = {"intro": {"count": 24, "body": proof}}
        count -= 24
    return {"intro": {"count": count, "body": proof}} if count else proof


def conjunction(target, premise_count):
    """Split a nested conjunction and lift each obligation under the same telescope."""
    if len(target) > MAX_GOAL_CHARS or premise_count > 23:
        return None
    binders, body = _peel(canonical(target))
    leaves = {}
    try:
        shape = _conjuncts(body, leaves)
    except ValueError:
        return None
    if not isinstance(shape, dict) or (not binders and len(leaves) < 3):
        return None
    # A partially parsed forall must stay intact and cannot become an obligation with missing binders.
    if not binders and canonical(target) != body:
        return None
    count = sum(binder.count for binder in binders)
    offset = premise_count + len(leaves)
    terms = [{"use": index} for index in range(offset, offset + count)]
    branch = _apply_leaves(shape, premise_count, terms)
    headers = "".join(binder.prefix for binder in binders)
    return {
        "format_version": 1,
        "obligations": [headers + f"({leaf})" if binders else leaf for leaf in leaves],
        "proof": introduce(offset + count, branch),
    }



def disjunction(target, premise_count):
    """Propose independent sufficient routes through each disjunct of a scoped goal."""
    if len(target) > MAX_GOAL_CHARS or premise_count > 23:
        return ()
    binders, body = _peel(canonical(target))
    split = _operator(body)
    if split is None or split[0] != "∨":
        return ()
    # A parser that dropped a partial binder telescope may not construct a route.
    if not binders and canonical(target) != body:
        return ()
    count = sum(binder.count for binder in binders)
    headers = "".join(binder.prefix for binder in binders)
    term = {"use": premise_count}
    if count:
        term = {"apply": [term, *({"use": index}
                                 for index in range(premise_count + 1, premise_count + 1 + count))]}
    size = premise_count + 1 + count
    return tuple({"format_version": 1,
                  "obligations": [headers + f"({part})" if binders else part],
                  "proof": introduce(size, {tag: term})}
                 for tag, part in zip(("left", "right"), split[1:]))
