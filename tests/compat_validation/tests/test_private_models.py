"""Check private profiles, direct loopback transport, and provider cost provenance."""

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from threading import Thread
import unittest
from unittest.mock import patch

from research_multiplier.model_adapters import ModelConfig, complete_structured
from research_multiplier.models.model_errors import ModelFailure


class PrivateModelTests(unittest.TestCase):
    def test_private_profile_refuses_remote_or_ambiguous_routes(self):
        for provider, url, model in (
            ("openai-compatible", "http://127.0.0.1:11434", "local"),
            ("ollama-native", "https://inference.example.com", "local"),
            ("ollama-native", "http://localhost:11434", "local"),
            ("ollama-native", "http://127.0.0.1:11434/proxy", "local"),
            ("ollama-native", "http://127.0.0.1:11434", "model:cloud"),
        ):
            with self.subTest(provider=provider, url=url, model=model):
                with self.assertRaises(ModelFailure):
                    ModelConfig(provider, model, "fixture", url, private=True)
        with patch.dict(os.environ, {"RESEARCH_MULTIPLIER_PROVER_PROVIDER": "ollama-native"}, clear=True):
            with self.assertRaisesRegex(ModelFailure, "PRIVATE_EXPLORE_PROVIDER"):
                ModelConfig.from_env("EXPLORE", private=True)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ModelFailure, "Explicitly approve"):
                ModelConfig("custom", "local", "fixture", "", adapter_factory="local_pkg:open_model", private=True)

    def test_native_local_model_does_not_use_proxy_and_keeps_reported_cost(self):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                received.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                response = json.dumps({"model": "fixture:latest", "message": {"content": '{"ok":true}'},
                                       "done": True, "done_reason": "stop", "eval_count": 4,
                                       "cost_usd": "0.000013"}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

        with HTTPServer(("127.0.0.1", 0), Handler) as service:
            thread = Thread(target=service.serve_forever)
            thread.start()
            try:
                env = {"http_proxy": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9",
                       "no_proxy": "", "NO_PROXY": ""}
                with patch.dict(os.environ, env):
                    config = ModelConfig("ollama-native", "fixture", "fixture",
                                         f"http://127.0.0.1:{service.server_port}", private=True)
                    result = complete_structured(config, "private-transport-fixture", "System", {"private": True}, 30, 3)
            finally:
                service.shutdown()
                thread.join()

        self.assertEqual(result.output, {"ok": True})
        self.assertEqual(result.metadata["parameters"]["private_model"], config.private_provenance())
        self.assertEqual(result.metadata["cost_usd"], "0.000013")
        self.assertEqual(result.transcript["response"]["usage"]["cost_usd"], "0.000013")
        path, body = received[0]
        self.assertEqual(path, "/api/chat")
        self.assertEqual(body["model"], "fixture")
        self.assertEqual(body["options"]["num_predict"], 30)
        self.assertEqual(json.loads(body["messages"][1]["content"]), {"private": True})
