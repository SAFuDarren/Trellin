"""Compatibility entry for a public path or byte-pinned component."""

import sys

from research_multiplier.policy import basis as _implementation

sys.modules[__name__] = _implementation
