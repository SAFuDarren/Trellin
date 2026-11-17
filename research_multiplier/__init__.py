"""Local research workflows with explicit evidence."""

from pathlib import Path as _Path

# Keep identified verifier and historical runner sources byte-for-byte.
# Restrict the compatibility entries to this package's normal path.
_root = _Path(__file__).resolve().parent
__path__.extend(str(_root / item) for item in (
    'proof/_pinned',
    'domain_packages/_pinned',
    'models/_pinned',
    'workflows/_pinned',
    'evaluations/_pinned',
    '_compat',
))
del _root, _Path
