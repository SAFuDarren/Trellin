"""Run versioned regressions against the installed Research Multiplier wheel."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from importlib.metadata import PackageNotFoundError, distribution
import os
from pathlib import Path
import subprocess
import sys
import unittest
from uuid import uuid4


_OPTIONAL = {
    "set RM_TEST_CASES_DIR to the external flow case bundle",
    "set RM_TEST_CASES_DIR to the external flow case bundle and prepare its Lean project",
    "supply two trusted installed Lean worker builds",
}

_REQUIRED_LEAN = (
    "test_target_pipeline.TargetPipelineTests.test_two_exact_approvals_and_a_checked_refutation_leave_the_original_open",
    "test_target_pipeline.TargetPipelineTests.test_native_auditor_rejects_an_unregistered_imported_fact",
    "test_lean_source.LeanSourceTests.test_plain_source_is_checked_against_frozen_consequent_and_all_premises",
    "test_lean_source.LeanSourceTests.test_built_lake_declaration_and_file_share_the_reviewed_basis",
    "test_lake_scope.LakeScopeTests.test_proof_rebuild_reverifies_but_changed_meaning_requires_review",
    "test_final_local_lemmas.FinalLocalLemmaTests.test_mathlib_chain_proves_frozen_goal_then_transitively_becomes_stale",
    "test_native_theorem_reuse.NativeTheoremReuseTests.test_plain_file_helper_replays_with_a_projected_premise",
    "test_cross_working_lemmas.CrossWorkingLemmaTests.test_replays_source_dependency_graph_then_invalidates_destination_after_ancestor_withdrawal",
    "test_exact_lean_negation.ExactLeanNegationTests.test_inconsistent_premise_cannot_refute_the_frozen_implication",
    "test_lean_finite_counterexample.FiniteCounterexampleIntegrationTests.test_witness_refutes_the_frozen_implication_under_its_original_hypothesis",
    "test_final_lean_report_retirement.FinalLeanReportRetirementTests.test_withdrawal_marks_an_already_adopted_claim_for_recheck",
    "test_evidence_conflict.EvidenceConflictTests.test_promotion_rechecks_conflicts_after_concurrent_issuance",
    "test_lean_model_context.PublicLeanContextTests.test_replayed_prerequisite_is_local_only_until_source_premise_is_released",
    "test_lean_dispatch_recovery.LeanDispatchRecoveryTests.test_expired_verification_recovers_same_candidate_without_another_model_call",
    "test_lean_dispatch_recovery.LeanDispatchRecoveryTests.test_exhausted_verification_stops_without_advancing_the_model_round",
    "test_lean_dispatch_recovery.LeanDispatchRecoveryTests.test_temporary_database_failure_after_lease_resumes_the_same_unsent_job",
    "test_lean_dispatch_recovery.LeanDispatchRecoveryTests.test_attempt_that_may_have_reached_the_provider_is_not_automatically_retried",
    "test_formalpedia_review.FormalpediaReviewTests.test_whole_goal_provenance_rolls_back_with_its_receipt",
)

_RUN_ONE = """
import sys
import unittest

suite = unittest.defaultTestLoader.loadTestsFromName(sys.argv[1])
result = unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful() or result.testsRun != 1:
    raise SystemExit(1)
if result.skipped:
    raise SystemExit(3 if result.skipped[0][1] in sys.argv[2:] else 1)
"""


def _test_ids(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from _test_ids(test)
        else:
            yield test.id()


def _native(directory, dsn, names, *, workers, required):
    import psycopg
    from psycopg import sql
    from sqlalchemy.engine import make_url
    from research_multiplier.storage.migrate import upgrade

    address = make_url(dsn)
    if address.drivername != "postgresql":
        raise ValueError("Use a postgresql:// test DSN for the native regressions.")
    prefix = "rm_regression_" + uuid4().hex[:16]
    template = prefix + "_seed"

    def database_dsn(name):
        return address.set(database=name).render_as_string(hide_password=False)

    def execute(statement):
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(statement)

    def drop(name):
        execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))

    def run(index, name):
        test_database = f"{prefix}_{index}"
        execute(sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
            sql.Identifier(test_database), sql.Identifier(template),
        ))
        try:
            env = {**os.environ, "RM_TEST_DSN": database_dsn(test_database),
                   "PYTHONDONTWRITEBYTECODE": "1"}
            result = subprocess.run(
                [sys.executable, "-c", _RUN_ONE, name, *sorted(_OPTIONAL)],
                cwd=directory, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, timeout=900,
            )
            return name, result.returncode, result.stdout
        except subprocess.TimeoutExpired:
            return name, 1, "The isolated native test exceeded its 900-second limit.\n"
        finally:
            drop(test_database)

    execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(template)))
    try:
        upgrade(database_dsn(template))
        passed = skipped = failed = 0
        print(f"Running {len(names)} installed-wheel Lean cases with separate databases.", flush=True)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(run, index, name) for index, name in enumerate(names, 1)]
            for future in as_completed(futures):
                name, code, output = future.result()
                print(f"--- {name}\n{output}", end="", flush=True)
                if code == 0:
                    passed += 1
                elif code == 3 and not required:
                    skipped += 1
                else:
                    failed += 1
        print(f"Native Lean result: {passed} passed, {skipped} optional skips, {failed} failed.", flush=True)
        return failed != 0
    finally:
        drop(template)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("offline", "postgres", "lean", "release-lean"))
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=1, help="Isolated Lean test processes.")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    expected = (root / "PACKAGE_VERSION").read_text(encoding="ascii").strip()
    try:
        installed = distribution("research-multiplier")
    except PackageNotFoundError:
        parser.error(f"Install the Research Multiplier {expected} wheel before running this suite.")
    if installed.version != expected:
        parser.error(f"This suite requires Research Multiplier {expected}; found {installed.version}.")

    sys.dont_write_bytecode = True
    import research_multiplier

    package = Path(installed.locate_file("research_multiplier/__init__.py")).resolve()
    if Path(research_multiplier.__file__).resolve() != package:
        parser.error("The suite must import the package from the selected wheel installation.")
    print(f"Research Multiplier {installed.version} from {package}", flush=True)
    dsn = os.environ.get("RM_TEST_DSN")
    if args.mode != "offline" and not dsn:
        parser.error("Set RM_TEST_DSN to a disposable PostgreSQL database with pgvector available.")

    directory = root / "tests"
    if args.mode in {"lean", "release-lean"}:
        if not os.environ.get("RM_TEST_LEAN_PROJECT"):
            parser.error("Set RM_TEST_LEAN_PROJECT to a prepared project in the pinned Lean environment.")
        directory /= "lean_flow"
        required = args.mode == "release-lean"
        names = _REQUIRED_LEAN if required else tuple(_test_ids(unittest.defaultTestLoader.discover(str(directory))))
        if not names or len(set(names)) != len(names):
            parser.error("The Lean suite must discover distinct test IDs.")
        return _native(directory, dsn, names, workers=args.workers, required=required)
    if args.mode == "offline":
        os.environ.pop("RM_TEST_DSN", None)

    tests = unittest.defaultTestLoader.discover(str(directory))
    result = unittest.TextTestRunner(verbosity=2).run(tests)
    skip_reasons = sorted({reason for _, reason in result.skipped})
    if skip_reasons:
        print("Skipped test prerequisites:")
        for reason in skip_reasons:
            print(f"  {reason}")
    unexpected = set(skip_reasons) - _OPTIONAL
    return not (result.wasSuccessful() and result.testsRun and (not unexpected or args.mode == "offline"))


if __name__ == "__main__":
    raise SystemExit(main())
