"""Compatibility entry for a public path or byte-pinned component."""

import sys

from research_multiplier.workflows import formalization as _implementation

sys.modules[__name__] = _implementation
