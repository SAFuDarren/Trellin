# Research Multiplier compatibility regression suite

This suite checks the installed wheel for the exact version in `PACKAGE_VERSION`. Create a clean virtual environment outside the checkout and real research workspaces. Install the matching wheel with the `postgres` extra and run from outside the checkout with `PYTHONPATH` unset.

Trellin's signed macOS bundle uses its own Python for normal app startup. Use a separate wheel environment for this suite, its subprocesses, disposable databases and test records. See [validating the macOS build](../../docs/desktop.md#validate-the-signed-build).

```sh
python /path/to/validation/run_regressions.py offline
RM_TEST_DSN='postgresql://DISPOSABLE_TEST_DATABASE' \
  python /path/to/validation/run_regressions.py postgres
RM_TEST_DSN='postgresql://DISPOSABLE_TEST_DATABASE' \
RM_TEST_LEAN_PROJECT=/path/to/prepared/pinned-lean-project \
  python /path/to/validation/run_regressions.py release-lean --workers 2
```

The PostgreSQL lanes need an empty disposable database with pgvector and a test superuser. The Lean lane also needs `lean` and `lake` 4.19.0 on `PATH` and the compiled imports in the prepared project. Supply `RM_TEST_CASES_DIR` for the historical fixed-network case and `RM_TEST_FLOW_PROJECT` if its compiled Lean project is elsewhere. Supply `RM_TEST_LEAN_BASELINE_WHEEL_INSTALL` and `RM_TEST_LEAN_CANDIDATE_WHEEL_INSTALL` to exercise the optional comparison between independently installed Lean worker builds. The `lean` and `release-lean` lanes use a separately cloned PostgreSQL database per test. `release-lean` executes the required cases and fails on any skip. `lean` discovers all native cases and reports skips only for the documented optional external inputs. Use `--workers 1` to run the native cases serially.

For the native lane, put PostgreSQL client tools matching the disposable server
on `PATH`. Backup, restore, and shadow comparison exercise those tools.

Compare an earlier and current installed wheel by installing each into its own
directory and running `python check_cli_compatibility.py PREVIOUS CURRENT`.
For release checks use the supplied `cli-baseline.json` as PREVIOUS.
This checks original command defaults, arguments, roles and workspace controls,
and the historic verifier sources.

## License

The validation code and its documentation are licensed under the [MIT License](LICENSE).
