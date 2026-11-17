"""Compatibility entry for a public path or byte-pinned component."""

import sys

from research_multiplier.workflows import research_progress as _implementation

sys.modules[__name__] = _implementation
