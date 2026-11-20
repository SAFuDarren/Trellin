"""Preserve the worker across retryable infrastructure failures."""

from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
import json
import unittest
from unittest.mock import Mock, patch

from research_multiplier.cli import advance_cli
from research_multiplier.storage.postgres import DatabaseOperationError
from research_multiplier.workflows.runtime import VerificationQueueError


class WorkerRecoveryTests(unittest.TestCase):
    def test_watch_reconnects_after_database_fault_without_repeating_success(self):
        args = Namespace(watch=3, command="advance", advance_action="worker", stage="verifier")
        watcher = Mock()
        ended = {"attempts_started": 1, "observations": 0, "errors": []}
        transient = DatabaseOperationError("verifier", "57P01", retryable=True)
        cycles = [transient, ended, KeyboardInterrupt()]
        output = StringIO()
        with patch.object(advance_cli, "Store") as store, patch.object(advance_cli, "_cycle", side_effect=cycles),                 patch.object(advance_cli, "sleep") as sleep, redirect_stdout(output):
            self.assertEqual(advance_cli._watch(args, watcher), 0)
        self.assertEqual(store.call_count, 2)
        watcher.reset_observations.assert_called_once()
        self.assertEqual([call.args for call in sleep.call_args_list], [(3,), (3,)])
        retry, completed = map(json.loads, output.getvalue().splitlines())
        self.assertEqual(retry["status"], "database_retry")
        self.assertEqual(completed, ended)

    def test_watch_reports_a_lost_verification_lease_and_continues(self):
        args = Namespace(watch=3, command="advance", advance_action="worker", stage="combined")
        recovered = {"attempts_started": 1, "observations": 0, "errors": []}
        output = StringIO()
        cycles = [VerificationQueueError("Expired lease", retryable=True), recovered, KeyboardInterrupt()]
        with (patch.object(advance_cli, "Store"),
              patch.object(advance_cli, "_cycle", side_effect=cycles),
              patch.object(advance_cli, "sleep"), redirect_stdout(output)):
            self.assertEqual(advance_cli._watch(args, Mock()), 0)
        retry, completed = map(json.loads, output.getvalue().splitlines())
        self.assertEqual(retry["status"], "verification_retry")
        self.assertEqual(completed, recovered)

    def test_permanent_permissions_error_surfaces_immediately(self):
        args = Namespace(watch=1, command="advance", advance_action="worker", stage="verifier")
        with patch.object(advance_cli, "Store"), patch.object(advance_cli, "_cycle",
                side_effect=DatabaseOperationError("verifier", "42501")), patch.object(advance_cli, "sleep") as sleep:
            with self.assertRaises(DatabaseOperationError):
                advance_cli._watch(args, Mock())
        sleep.assert_not_called()
