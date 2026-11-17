"""Compatibility entry for a public path or byte-pinned component."""

import sys

from research_multiplier.evaluations import lean_shadow as _implementation

sys.modules[__name__] = _implementation
