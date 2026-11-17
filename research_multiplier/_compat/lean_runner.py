"""Compatibility entry for a public path or byte-pinned component."""

import sys

from research_multiplier.proof import lean_runner as _implementation

sys.modules[__name__] = _implementation
