import json
import subprocess
import unittest
from unittest.mock import patch

from research_multiplier.models.cli_executor import complete


class CliExecutorTests(unittest.TestCase):
    def test_codex_keeps_only_fresh_prompt_and_reports_usage(self):
        response = [
            {"type": "thread.started", "thread_id": "sample-thread"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "{}"}},
            {"type": "turn.completed", "usage": {"input_tokens": 19, "output_tokens": 4}},
        ]
        outputs = [
            subprocess.CompletedProcess([], 0, "codex sample version\n", ""),
            subprocess.CompletedProcess([], 0, "\n".join(map(json.dumps, response)), ""),
        ]
        with patch("research_multiplier.models.cli_executor.subprocess.run", side_effect=outputs) as run, patch.dict(
            "os.environ", {"RM_VERIFIER_DSN": "fixture", "OPENAI_API_KEY": "fixture"}
        ):
            result = complete("codex-cli", "codex", "sample-model", [{"role": "user", "content": "public"}],
                              10, reasoning_effort="low")
        options = run.call_args.kwargs
        self.assertIn('model_reasoning_effort="low"', run.call_args.args[0])
        self.assertNotIn("RM_VERIFIER_DSN", options["env"])
        self.assertNotIn("OPENAI_API_KEY", options["env"])
        self.assertEqual(options["input"], "USER\npublic")
        self.assertEqual(result["usage"], {"prompt_tokens": 19, "completion_tokens": 4})

    def test_claude_records_login_failure_without_returning_raw_process_output(self):
        outputs = [
            subprocess.CompletedProcess([], 0, "Claude Code sample version\n", ""),
            subprocess.CompletedProcess([], 1, "Not logged in. Confidential fixture output.", ""),
        ]
        with patch("research_multiplier.models.cli_executor.subprocess.run", side_effect=outputs):
            with self.assertRaisesRegex(ValueError, "not signed in") as caught:
                complete("claude-code", "claude", "sample-model", [], 10)
        self.assertNotIn("Confidential fixture output", str(caught.exception))

    def test_version_probe_consumes_the_same_deadline(self):
        version = subprocess.CompletedProcess([], 0, "sample version\n", "")
        with patch("research_multiplier.models.cli_executor.monotonic", side_effect=[0, 1, 4]), patch(
            "research_multiplier.models.cli_executor.subprocess.run", return_value=version
        ) as run:
            with self.assertRaisesRegex(ValueError, "execution budget"):
                complete("claude-code", "claude", "sample-model", [], 3)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.kwargs["timeout"], 2)
