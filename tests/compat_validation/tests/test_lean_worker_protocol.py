"""Check the isolated Lean worker's versioned process boundary."""

from io import StringIO
import unittest
from unittest.mock import MagicMock, patch

from research_multiplier.evaluations import lean_shadow, lean_worker


class LeanWorkerProtocolTests(unittest.TestCase):
    def test_request_roundtrip_and_mode_validation(self):
        request = {"action": "inspect", "workspace": "/scratch",
                   "identity": {}, "scope": {}, "options": {}}
        self.assertEqual(lean_worker._read_request(StringIO(lean_worker.request_text(request))), request)
        incomplete_run = {**request, "action": "run"}
        with self.assertRaisesRegex(ValueError, "invalid action or proof context"):
            lean_worker._read_request(StringIO(lean_worker.request_text(incomplete_run)))

    def test_rejects_incompatible_version_and_nonobject_result(self):
        with self.assertRaisesRegex(ValueError, "incompatible protocol version"):
            lean_worker.read_response('{"protocol_version":2,"result":{}}', 0)
        with self.assertRaisesRegex(ValueError, "incompatible protocol result"):
            lean_worker.read_response('{"protocol_version":1,"result":7}', 0)
        self.assertEqual(lean_worker.read_response('{"protocol_version":1,"result":null}', 0),
                         {"result": None})

    def test_dispatch_rejects_valid_looking_stdout_from_failed_worker(self):
        process = MagicMock()
        process.__enter__.return_value = process
        process.returncode = 17
        process.communicate.return_value = ('{"protocol_version":1,"result":{"outcome":"verified"}}',
                                            "diagnostic message")
        with patch.object(lean_shadow.subprocess, "Popen", return_value=process):
            with self.assertRaisesRegex(ValueError, "exited with status 17"):
                lean_shadow._dispatch({"workspace": "/scratch"}, {}, 1)
