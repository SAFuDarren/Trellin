"""Fixed component controls for isolated Lean evaluation workers."""

from contextlib import contextmanager
from contextvars import ContextVar


WORKING_REUSE = "cross-claim-working-reuse"
VERSIONS = {WORKING_REUSE: "1"}
_OMITTED = ContextVar("lean_shadow_omitted_component", default=None)


def control(name, *, enabled):
    """Describe one supported scaffolding switch and its registered version."""
    if not isinstance(name, str) or name not in VERSIONS or type(enabled) is not bool:
        raise ValueError("Select one supported Lean scaffolding component for ablation.")
    return {"name": name, "version": VERSIONS[name], "enabled": enabled}


def enabled(name):
    return name != _OMITTED.get()


@contextmanager
def evaluation_control(setting):
    if setting is None:
        yield None
        return
    if not isinstance(setting, dict) or set(setting) != {"name", "version", "enabled"}:
        raise ValueError("The Lean shadow worker received an invalid component control.")
    confirmed = control(setting["name"], enabled=setting["enabled"])
    if confirmed != setting:
        raise ValueError("The Lean shadow worker loaded a different component version.")
    token = _OMITTED.set(None if confirmed["enabled"] else confirmed["name"])
    try:
        yield {**confirmed, "enabled": enabled(confirmed["name"])}
    finally:
        _OMITTED.reset(token)
