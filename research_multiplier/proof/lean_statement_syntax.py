"""Find Lean syntax that the statement audit rejects, before anything is frozen (#296).

The authoritative check is the pinned Lean audit (`lean_support/TheoryAudit.lean`), which accepts only a fixed
subset of Core term syntax because other term elaborators can run code before the kernel. That audit runs when the
exact statement is drafted, after the researcher has already frozen the target and confirmed its basis. This module
recognises the constructs researchers most often write in Mathlib-style statements and names an accepted rewrite,
so intake can stop them first. It never accepts anything the audit would reject: it only reports constructs that the
audit is known to refuse.
"""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Finding:
    text: str
    kind: str
    rewrite: str

    def __str__(self):
        return f"`{self.text}` ({self.kind}): {self.rewrite}"


_INSTANCE = re.compile(r"\[\s*(?:([A-Za-z_][A-Za-z_0-9']*)\s*:\s*)?([^\[\]]+?)\s*\]")
_ABSOLUTE = re.compile(r"\|([^|]+)\|")
_SORT_STAR = re.compile(r"\b(Type|Sort)\s*\*")
_KEYWORDS = re.compile(r"(?<![\w.'])(fun|let|have|show|if|match|by|do)(?![\w'])")
_LITERAL = re.compile(r'"[^"]*"')

# Symbols outside the accepted Core term syntax, with the syntax kind the audit sees and an accepted rewrite.
_SYMBOLS = {
    "ℕ": ("termℕ", "write `Nat`"),
    "ℤ": ("termℤ", "write `Int`"),
    "ℚ": ("termℚ", "write `Rat`"),
    "ℝ": ("termℝ", "write `Real`"),
    "ℂ": ("termℂ", "write `Complex`"),
    "∑": ("BigOperators.bigsum", "write `Finset.sum s f`, or name the sum in a definition"),
    "∏": ("BigOperators.bigprod", "write `Finset.prod s f`, or name the product in a definition"),
    "∈": ("term_∈_ or binderTerm∈_", "write `Membership.mem s x`; for `∃ x ∈ s, P` write `∃ x, Membership.mem s x ∧ P`"),
    "∉": ("term_∉_", "write `¬ Membership.mem s x`"),
    "∅": ("term∅", "write `EmptyCollection.emptyCollection`, or name the empty set in a definition"),
    "⊥": ("term⊥", "write `Bot.bot`"),
    "⊤": ("term⊤", "write `Top.top`"),
    "⊆": ("term_⊆_", "write `HasSubset.Subset s t`"),
    "∩": ("term_∩_", "write `Inter.inter s t`"),
    "∪": ("term_∪_", "write `Union.union s t`"),
    "∘": ("term_∘_", "write `Function.comp f g`"),
    "⁻¹": ("term_⁻¹", "write `Inv.inv x`"),
    "•": ("term_•_", "write `HSMul.hSMul a x`"),
    "·": ("cdot", "write the function out, or name it in a definition"),
    "λ": ("Lean.Parser.Term.fun", "name the function in a definition"),
    "↦": ("Lean.Parser.Term.fun", "name the function in a definition"),
    "⟨": ("Lean.Parser.Term.anonymousCtor", "write the constructor by name, for example `Prod.mk a b`"),
    "×": ("term_×_", "write `Prod A B`"),
    "≈": ("term_≈_", "write `HasEquiv.Equiv a b`"),
    "≃": ("term_≃_", "write `Equiv A B`"),
    "→+": ("Mathlib morphism notation", "write the structure by name, for example `AddMonoidHom A B`"),
}
_ASCII = {
    ":=": ("Lean.Parser.Term.let or structure instance", "name the value in a definition"),
    "==": ("term_==_", "write `=`"),
    "&&": ("term_&&_", "write `∧` for propositions"),
    "||": ("term_||_", "write `∨` for propositions"),
    "<|": ("term_<|_", "use parentheses"),
    "|>": ("term_|>_", "use parentheses"),
    "$": ("term_$_", "use parentheses"),
    "@": ("Lean.Parser.Term.explicit", "pass the arguments without `@`, with type ascriptions where needed"),
    "#": ("Lean.Parser.Term.hash syntax", "remove it"),
    "?": ("Lean.Parser.Term.syntheticHole", "write the term out"),
}


def check(proposition):
    """Return the constructs in one Lean proposition or type that the statement audit is known to reject."""
    text = proposition if isinstance(proposition, str) else ""
    found = []
    for match in _LITERAL.finditer(text):
        found.append(Finding(match.group(0), "string literal", "the audit accepts no string literals"))
    text = _LITERAL.sub(" ", text)
    for match in _INSTANCE.finditer(text):
        name, body = match.group(1) or "name", match.group(2)
        if "," in body and not match.group(1):
            found.append(Finding(match.group(0), "list literal", "name the list in a definition"))
        else:
            found.append(Finding(match.group(0), "instance binder, Lean.Parser.Term.instBinder",
                                 f"write `({name} : {body})`"))
    unmatched = _INSTANCE.sub(" ", text)
    if "[" in unmatched or "]" in unmatched:
        found.append(Finding("[", "bracket syntax", "write instance binders as `(name : Class X)`"))
    for match in _ABSOLUTE.finditer(text.replace("||", "  ")):
        found.append(Finding(match.group(0), "absolute value, «term|___|»", f"write `abs ({match.group(1).strip()})`"))
    for match in _SORT_STAR.finditer(text):
        found.append(Finding(match.group(0), "universe placeholder", f"write `{match.group(1)}`"))
    for match in _KEYWORDS.finditer(text):
        found.append(Finding(match.group(1), f"`{match.group(1)}` term", "name the value or function in a definition"))
    for symbol, (kind, rewrite) in {**_SYMBOLS, **_ASCII}.items():
        if symbol in text:
            found.append(Finding(symbol, kind, rewrite))
    return list(dict.fromkeys(found))


def _lean_texts(value, path=""):
    """Yield every Lean proposition and shared parameter type in a target, notes or item document."""
    if isinstance(value, dict):
        lean = value.get("lean") if isinstance(value.get("lean"), dict) else None
        if lean is not None:
            if isinstance(lean.get("proposition"), str):
                yield f"{path}.lean.proposition".lstrip("."), lean["proposition"]
            for index, parameter in enumerate(lean.get("parameters") or []):
                if isinstance(parameter, dict) and isinstance(parameter.get("type"), str):
                    yield f"{path}.lean.parameters[{index}].type".lstrip("."), parameter["type"]
        for key, child in value.items():
            if key != "lean":
                yield from _lean_texts(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _lean_texts(child, f"{path}[{index}]")


def require_supported(document):
    """Raise before freezing or drafting when any Lean proposition uses syntax the statement audit rejects."""
    problems = [(where, finding) for where, text in _lean_texts(document) for finding in check(text)]
    if problems:
        lines = [f"- {where}: {finding}" for where, finding in problems]
        raise ValueError(
            "The Lean statement audit does not accept this syntax, so the statement could never be checked. "
            "Rewrite it before freezing:\n" + "\n".join(lines)
            + "\nSee 'Accepted proposition syntax' in docs/lean-core.md."
        )


def explain(propositions):
    """Name the rejected constructs after the Lean audit refused a frozen statement, or return None."""
    problems = [str(finding) for text in propositions for finding in check(text)]
    if not problems:
        return None
    return "The frozen statement uses syntax the Lean statement audit does not accept: " + "; ".join(problems) + "."
