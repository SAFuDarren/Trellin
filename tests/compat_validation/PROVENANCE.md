# Compatibility regression suite

This directory preserves the test source and CLI baseline from the verified
Research Multiplier 1.0.0 validation archive. The copied files retain their
original bytes and [MIT license](LICENSE). The archive SHA-256 is
`8cf431e6c89d26a0db705883c720f8459d1d1040710b0346011bfe2b90c0bd0b`.

Run `run_regressions.py` with the installed wheel and the same disposable
PostgreSQL and pinned Lean environment as the [maintained test lanes](../README.md).
The runner owns discovery, case isolation and its required and optional
prerequisites. Pytest runs the current test modules separately.

Public examples, historical inputs and test output remain in their external
locations. New repository tests go in the corresponding current lane under
`tests/`.
