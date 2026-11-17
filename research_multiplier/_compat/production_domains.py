"""Compatibility entry for a public path or byte-pinned component."""

import sys

from research_multiplier.domain_packages import production_domains as _implementation

sys.modules[__name__] = _implementation
