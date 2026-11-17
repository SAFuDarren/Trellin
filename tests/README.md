# Tests

Lanes are pytest markers (docs/engineering.md, Testing strategy):

| Marker | Needs | Run |
| --- | --- | --- |
| `offline` | Python only | `python -m pytest -m offline` |
| `postgres` | `RM_TEST_DSN` for a disposable PostgreSQL database with pgvector | `RM_TEST_DSN=... python -m pytest -m postgres` |
| `lean` | The pinned Lean toolchain and Mathlib | `python -m pytest -m lean` |

Every test module sets `pytestmark` to its lane. Tests never touch a real research workspace or database, and fixtures contain no credentials or real research content.

The external validation bundle (see CONTRIBUTING.md) still holds the release regressions; roadmap task P0.2 moves them here.
