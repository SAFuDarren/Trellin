"""Read supported Formalpedia declaration types without executing their proof bodies."""

from dataclasses import dataclass
import re


MAX_PROPOSITION_CHARS = 16_000
_IDENTIFIER = r"[A-Za-z_][A-Za-z_0-9']*(?:\.[A-Za-z_][A-Za-z_0-9']*)*"
_DECLARATION = re.compile(r"(?m)^[ \t]*theorem[ \t]+([^\s:({\[]+)")
_NAMESPACE = re.compile(rf"namespace[ \t]+({_IDENTIFIER})")
_IMPORT = re.compile(rf"import[ \t]+({_IDENTIFIER})")
_OPTION = re.compile(r"set_option[ \t]+autoImplicit[ \t]+false")
_STUB = re.compile(r":=\s*by\s+sorry\s*\Z")
_OPENERS = {"(": ")", "{": "}", "[": "]"}


@dataclass(frozen=True)
class ExportedTheorem:
    proposition: str
    namespace: str | None
    imports: tuple[str, ...]


def _uncomment(text):
    pieces = []
    start = index = depth = 0
    quote = None
    while index < len(text):
        pair = text[index:index + 2]
        character = text[index]
        if depth:
            if pair == "/-":
                depth += 1
                index += 2
                continue
            if pair == "-/":
                depth -= 1
                index += 2
                if not depth:
                    start = index
                continue
        elif quote:
            if quote == '"' and character == "\\":
                index += 2
                continue
            if character == quote:
                quote = None
        elif pair in ("--", "/-"):
            pieces.extend((text[start:index], "\n"))
            if pair == "--":
                end = text.find("\n", index)
                index = start = len(text) if end < 0 else end
            else:
                depth = 1
                index += 2
            continue
        elif character in ('"', '«'):
            quote = '"' if character == '"' else '»'
        index += 1
    if depth or quote:
        raise ValueError("The exported Lean source has an incomplete comment or quotation.")
    return "".join((*pieces, text[start:]))


def _declaration(text, name, *, single):
    source = _uncomment(text).strip()
    declarations = _DECLARATION.finditer(source)
    declaration = next(declarations, None)
    if declaration is None or (single and next(declarations, None) is not None):
        raise ValueError("Replay requires one plain exported theorem with its exact catalog name.")
    namespace = None
    imports = []
    commands_started = False
    for raw_line in source[:declaration.start()].splitlines():
        line = raw_line.strip()
        if not line:
            continue
        module = _IMPORT.fullmatch(line)
        if module and not commands_started:
            if module[1].startswith("Definitions."):
                raise ValueError("This export depends on external definitions that need a reviewed local port.")
            imports.append(module[1])
            continue
        commands_started = True
        if _OPTION.fullmatch(line):
            continue
        wrapper = _NAMESPACE.fullmatch(line)
        if namespace is not None or wrapper is None:
            raise ValueError("Exported notation or scoped declarations need a reviewed local port before replay.")
        namespace = wrapper[1]
    declared = declaration[1]
    qualified = (declared.removeprefix("_root_.") if namespace is None or declared.startswith("_root_.")
                 else f"{namespace}.{declared}")
    if qualified != name:
        raise ValueError("Replay requires one plain exported theorem with its exact catalog name.")
    return source[declaration.end():], namespace, tuple(imports)


def _top_level_colon(text, *, assignment=False):
    closing = []
    quotes = None
    escaped = False
    for index, character in enumerate(text):
        if quotes:
            if character == quotes and not escaped:
                quotes = None
            escaped = quotes == '"' and character == "\\" and not escaped
        elif character in ('"', '«'):
            quotes = '"' if character == '"' else '»'
        elif character in _OPENERS:
            closing.append(_OPENERS[character])
        elif character in (")", "}", "]"):
            if not closing or closing.pop() != character:
                raise ValueError("The exported theorem has unbalanced delimiters in its type.")
        elif character == ":" and not closing and (not assignment or text.startswith(":=", index)):
            return index
    return None


def _proposition(signature):
    position = _top_level_colon(signature)
    if position is None:
        raise ValueError("The exported theorem has no explicit proposition.")
    binders = signature[:position].strip()
    body = signature[position + 1:].strip()
    if binders and not binders.startswith(("(", "{", "[")):
        raise ValueError("The exported theorem needs an explicit Lean binder header.")
    proposition = f"∀ {binders}, {body}" if binders else body
    if not body or len(proposition) > MAX_PROPOSITION_CHARS or "\x00" in proposition:
        raise ValueError("The exported proposition is outside the supported input size.")
    return proposition


def exported_theorem(text, name):
    """Read a plain exported theorem stub and preserve its lexical context."""
    tail, namespace, imports = _declaration(text, name, single=True)
    if namespace is not None:
        closing = re.search(r"(?m)^\s*end[ \t]+" + re.escape(namespace) + r"\s*\Z", tail)
        if closing is None:
            raise ValueError("The exported theorem must close its declared namespace.")
        tail = tail[:closing.start()]
    stub = _STUB.search(tail)
    if stub is None:
        raise ValueError("The exported statement does not end in the expected statement-only stub.")
    return ExportedTheorem(_proposition(tail[:stub.start()]), namespace, imports)


def exported_solution_type(text):
    """Read the annotated type of a plain accepted `theorem solution`."""
    tail, namespace, imports = _declaration(text, "solution", single=False)
    assignment = _top_level_colon(tail, assignment=True)
    if assignment is None:
        raise ValueError("The exported solution needs an explicitly typed proof declaration.")
    return ExportedTheorem(_proposition(tail[:assignment]), namespace, imports)
