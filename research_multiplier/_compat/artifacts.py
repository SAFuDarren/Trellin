"""Compatibility entry for a public path or byte-pinned component."""

import sys

from research_multiplier.storage import artifacts as _implementation

sys.modules[__name__] = _implementation
