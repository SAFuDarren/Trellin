import json
from socketserver import StreamRequestHandler, TCPServer
import sys
from threading import Event, Thread
from time import monotonic, sleep
import unittest
from unittest.mock import patch

from research_multiplier import v0a_models as models


def chat_reply(content, *, model="model-a", reason="stop"):
    return {
        "id": "request-a",
        "model": model,
        "choices": [{"message": {"content": content}, "finish_reason": reason}],
        "usage": {"prompt_tokens": 23, "completion_tokens": 9},
    }


class ModelAdapterTests(unittest.TestCase):
    def setUp(self):
        self.claim = {"sensitivity": "public", "statement": "A public fixture"}
        self.requirements = {"certificate": {"representation": "test"}}
        self.prover = models.ModelConfig("deepseek", "alias-a", "deepseek", "https://api.deepseek.com", "secret-a")
        self.auditor = models.ModelConfig("ollama", "qwen-test", "qwen35", "http://127.0.0.1:11434/v1")

    def test_chat_proof_records_provider_model_and_usage(self):
        candidate = {"proof_markdown": "Proof", "certificate": {"witness": 5}}
        with patch.object(models, "_post", return_value=chat_reply(json.dumps(candidate))) as post:
            run = models.prove(self.prover, self.claim, self.requirements, max_output_tokens=123)
        url, payload, headers, timeout = post.call_args.args
        self.assertEqual(url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer secret-a")
        self.assertEqual(payload["max_tokens"], 123)
        self.assertEqual(run.output, candidate)
        self.assertEqual(run.metadata["id"], "model-a")
        self.assertEqual(run.metadata["family"], "deepseek")
        self.assertEqual(run.metadata["output_tokens"], 9)
        self.assertEqual(len(run.metadata["prompt_sha256"]), 64)
        self.assertNotIn("secret-a", str(run.transcript))
        self.assertNotIn("secret-a", repr(self.prover))
        self.assertEqual(run.transcript["request"]["model_id"], "alias-a")
        self.assertEqual(run.transcript["model_contract_version"], 1)
        self.assertEqual(run.transcript["request"]["budget"], {
            "wall_seconds": 90, "max_attempts": 1, "max_model_calls": 1, "max_tokens": 123,
        })
        self.assertEqual(run.transcript["response"]["request_id"], "request-a")
        self.assertEqual(run.transcript["normalized_response"], {
            "provider": "deepseek", "requested_model": "alias-a", "model_id": "model-a",
            "family": "deepseek", "output": {"structured": candidate},
            "usage": {"input_tokens": 23, "output_tokens": 9},
        })

    def test_cli_records_requested_tokens_and_unsupported_cap(self):
        from research_multiplier.models import cli_executor

        candidate = {"proof_markdown": "Proof", "certificate": {"witness": 5}}
        config = models.ModelConfig("codex-cli", "alias", "openai", "codex", binary=sys.executable)
        reply = {**chat_reply(json.dumps(candidate)), "cli_version": "codex test version"}
        reply["usage"].update(cached_input_tokens=17, cost_usd="0.00123")
        with patch.object(cli_executor, "complete", return_value=reply):
            run = models.prove(config, self.claim, self.requirements, max_output_tokens=123)
        self.assertFalse(run.metadata["parameters"]["output_token_limit_enforced"])
        self.assertEqual(run.metadata["parameters"]["cli_version"], "codex test version")
        self.assertEqual(run.metadata["parameters"]["cached_input_tokens"], 17)
        self.assertEqual(run.metadata["cost_usd"], "0.00123")
        self.assertEqual(run.transcript["request"]["budget"]["max_tokens"], 123)

    def test_audit_excludes_candidate_attribution_and_uses_a_fresh_call(self):
        candidate = {
            "proof_markdown": "Proof",
            "certificate": {"witness": 5},
            "model_id": "prover-metadata",
            "transcript": {"content": "prover transcript"},
        }
        result = {"verdict": "verified", "diff_status": "exact", "reason": "Every step checks."}
        with patch.object(models, "_post", return_value=chat_reply(json.dumps(result), model="qwen-test")) as post:
            run = models.audit(self.auditor, self.claim, candidate, self.requirements)
        url, payload, headers, _ = post.call_args.args
        self.assertEqual(url, "http://127.0.0.1:11434/v1/chat/completions")
        self.assertEqual(len(payload["messages"]), 2)
        sent = json.loads(payload["messages"][1]["content"])
        self.assertEqual(set(sent["proof_candidate"]), {"proof_markdown", "certificate"})
        self.assertNotIn("Authorization", headers)
        self.assertEqual(run.output, result)
        self.assertEqual(run.metadata["family"], "qwen35")

    def test_messages_transport_and_token_fields(self):
        claude = models.ModelConfig("anthropic", "claude-test", "claude", "https://api.anthropic.com/v1", "secret")
        output = {"failure_reason": "Unresolved lemma"}
        reply = {
            "id": "msg-1",
            "model": "claude-test-exact",
            "content": [{"type": "text", "text": json.dumps(output)}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 31, "output_tokens": 7},
        }
        with patch.object(models, "_post", return_value=reply) as post:
            run = models.prove(claude, self.claim, self.requirements)
        url, payload, headers, _ = post.call_args.args
        self.assertEqual(url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(payload["messages"][0]["role"], "user")
        self.assertEqual(payload["system"], models.PROOF_SYSTEM)
        self.assertEqual(headers["x-api-key"], "secret")
        self.assertEqual(run.metadata["input_tokens"], 31)
        self.assertEqual(run.metadata["id"], "claude-test-exact")
        self.assertEqual(run.output, output)

    def test_native_ollama_returns_structured_output_and_usage(self):
        config = models.ModelConfig("ollama-native", "local", "local-family", "http://127.0.0.1:11434")
        candidate = {"proof_markdown": "Proof", "certificate": {"witness": 5}}
        reply = {
            "model": "local",
            "message": {"content": "```json\n" + json.dumps(candidate) + "\n```"},
            "done": True, "done_reason": "stop", "prompt_eval_count": 30, "eval_count": 9,
        }
        with patch.object(models, "_post", return_value=reply) as post:
            result = models.prove(config, self.claim, self.requirements, max_output_tokens=321)
        url, payload, _, _ = post.call_args.args
        self.assertEqual(url, "http://127.0.0.1:11434/api/chat")
        self.assertEqual(payload["options"]["num_predict"], 321)
        self.assertFalse(payload["think"])
        self.assertEqual(result.metadata["input_tokens"], 30)
        self.assertEqual(result.output, candidate)

    def test_alias_config_does_not_forward_default_key_to_custom_host(self):
        with patch.dict("os.environ", {
            "OPENAI_API_KEY": "official-only",
            "RESEARCH_MULTIPLIER_PROVER_PROVIDER": "openai",
            "RESEARCH_MULTIPLIER_PROVER_MODEL": "model-a",
            "RESEARCH_MULTIPLIER_PROVER_BASE_URL": "https://custom.example/v1",
            "RESEARCH_MULTIPLIER_PROVER_API_KEY": "",
            "RESEARCH_MULTIPLIER_PROVER_FAMILY": "openai",
        }):
            with self.assertRaisesRegex(ValueError, "Configure an API key"):
                models.ModelConfig.from_env("PROVER")

    def test_rejects_duplicate_keys_extra_fields_and_truncated_responses(self):
        for content, reason in (
            ('{"proof_markdown":"a","proof_markdown":"b","certificate":{"x":1}}', "stop"),
            ('{"proof_markdown":"a","certificate":{"x":NaN}}', "stop"),
            ('{"proof_markdown":"a","certificate":{"x":1},"verified":true}', "stop"),
            ('{"proof_markdown":"a","certificate":{"x":1}}', "length"),
        ):
            with self.subTest(content=content, reason=reason):
                with patch.object(models, "_post", return_value=chat_reply(content, reason=reason)):
                    with self.assertRaises(models.ModelOutputError) as caught:
                        models.prove(self.prover, self.claim, self.requirements)
                self.assertEqual(caught.exception.metadata["family"], "deepseek")
                self.assertEqual(caught.exception.transcript["response"]["text"], content)

    def test_restricts_context_and_rejects_same_family(self):
        with patch.object(models, "_post") as post:
            with self.assertRaisesRegex(ValueError, "public frozen claims"):
                models.prove(self.prover, {"sensitivity": "restricted"}, self.requirements)
        post.assert_not_called()
        models.validate_pair(self.prover, self.auditor)
        with self.assertRaisesRegex(ValueError, "different model families"):
            models.validate_pair(self.prover, self.prover)


class ModelHTTPTests(unittest.TestCase):
    def request(self, handler, timeout):
        with TCPServer(("127.0.0.1", 0), handler) as server:
            worker = Thread(target=server.handle_request, daemon=True)
            worker.start()
            try:
                return models._post(f"http://127.0.0.1:{server.server_address[1]}", {}, {}, timeout)
            finally:
                worker.join(2)

    def test_slow_drip_cannot_extend_wall_clock_deadline(self):
        connected = Event()

        class SlowReply(StreamRequestHandler):
            def handle(self):
                self.rfile.readline()
                connected.set()
                try:
                    self.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Length: 50\r\n\r\n{")
                    for _ in range(49):
                        sleep(0.03)
                        self.wfile.write(b" ")
                except OSError:
                    pass

        start = monotonic()
        with self.assertRaisesRegex(ValueError, "request deadline"):
            self.request(SlowReply, 0.2)
        self.assertTrue(connected.is_set())
        self.assertLess(monotonic() - start, 1.0)

    def test_malformed_status_is_a_normalized_transport_error(self):
        class MalformedReply(StreamRequestHandler):
            def handle(self):
                self.rfile.readline()
                self.wfile.write(b"NOT HTTP\r\n\r\n")

        with self.assertRaisesRegex(ValueError, "could not complete"):
            self.request(MalformedReply, 1)


if __name__ == "__main__":
    unittest.main()
