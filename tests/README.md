# Tests

Run the automated checks by lane. Test code lives here. Research projects, public
examples and generated test outputs use separate directories.

| Marker | Requirements | Command |
| --- | --- | --- |
| `offline` | Python with the development extras | `python -m pytest -m offline` |
| `postgres` | PostgreSQL with pgvector and the package's PostgreSQL extras | `python -m pytest -m postgres` |
| `lean` | The same PostgreSQL setup and Lean 4.19.0 on PATH | `python -m pytest -m lean` |

For either native lane, provide `RM_TEST_DSN` through a private environment for
an administrator on a disposable PostgreSQL cluster dedicated to the worktree.
The tests create and drop uniquely named databases. The Lean test also creates
temporary capability logins and generates its Lean Core input in the pytest
temporary directory.

For installed-package verification, install the wheel with the `dev` and
`postgres` extras in a clean environment. Change to a temporary directory,
unset `PYTHONPATH`, and run the repository tests by absolute path.

```sh
python -m pytest --import-mode=importlib -p no:cacheprovider \
  -m 'postgres or lean' /path/to/repository/tests
```

The [external validation bundle](../CONTRIBUTING.md#run-the-external-regression-suite)
continues to run the full release regressions. Roadmap task P0.2 tracks their
migration to the repository.
