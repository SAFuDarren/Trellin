"""A shared Lean binder scope pinned by each reviewed open proposition."""

import re

MAX_PARAMETERS = 16
MAX_TYPE_LENGTH = 2000
_PARAMETER = re.compile(r"[A-Za-z_][A-Za-z_0-9']{0,63}")


def parameters(formal):
    """Read typed parameters from the exact formal version, in binding order."""
    lean = formal.get("lean") if isinstance(formal, dict) else None
    if not isinstance(lean, dict) or "parameters" not in lean:
        return ()
    values = lean["parameters"]
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_PARAMETERS:
        raise ValueError("Declare one to sixteen shared Lean parameters with names and types.")
    names = set()
    for parameter in values:
        if not isinstance(parameter, dict) or set(parameter) != {"name", "type"}:
            raise ValueError("Each shared Lean parameter needs exactly a name and a type.")
        name, type_text = parameter["name"], parameter["type"]
        if (not isinstance(name, str) or not _PARAMETER.fullmatch(name) or name == "_" or name in names
                or not isinstance(type_text, str) or not 1 <= len(type_text) <= MAX_TYPE_LENGTH
                or not type_text.strip() or not type_text.isprintable()):
            raise ValueError("Shared Lean parameters need distinct explicit names and nonempty single-line types.")
        names.add(name)
    return tuple(values)


def prefix(shared):
    if not shared:
        return ""
    return "∀ " + " ".join(f"({row['name']} : {row['type']})" for row in shared) + ", "


def scoped_term(text, shared):
    return f"{prefix(shared)}(\n{text}\n)" if shared else text


class Hypotheses(list):
    """Keep the existing ordered premise list and its enclosing binder context."""

    def __init__(self, hypotheses, shared=()):
        super().__init__(hypotheses)
        self.parameters = shared

    @property
    def argument_count(self):
        return len(self.parameters) + len(self)


def premise_usage(contract, hypotheses, observed):
    """Pair each frozen assumption with its proof binder following the parameters."""
    offset = len(hypotheses.parameters)
    return [
        {"item": row["label"], "proposition": hypotheses[index],
         "occurs_in_proof_term": observed[offset + index] if offset + index < len(observed) else None}
        for index, row in enumerate(contract["hypotheses"])
    ]
