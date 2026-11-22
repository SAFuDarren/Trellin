# SPDX-License-Identifier: Apache-2.0

import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline

SPEC = importlib.util.spec_from_file_location(
    "model_releases", Path(__file__).resolve().parents[2] / "scripts" / "model_releases.py"
)
releases = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(releases)


def test_extract_language_model_ids_from_each_supported_catalog():
    openai = (
        "- [GPT](/api/docs/models/gpt-6.3-sol-2027-03-12.md)\n"
        "- [Other](/api/docs/models/gpt-6-cyber.md)\n"
        "- [Image](/api/docs/models/gpt-image-2.md)\n"
    )
    anthropic = "| Claude API ID | `claude-opus-6` | `claude-sonnet-6` |"
    google = "* *Model code* **gemini-5-pro**\n* *Model code* **gemini-5-live**"
    assert releases.model_ids("OpenAI", openai) == {"gpt-6.3-sol-2027-03-12"}
    assert releases.model_ids("Anthropic", anthropic) == {"claude-opus-6", "claude-sonnet-6"}
    assert releases.model_ids("Google", google) == {"gemini-5-pro"}


def test_only_new_models_without_a_pending_or_closed_issue_are_selected():
    catalogs = {"OpenAI": {"gpt-6.2-sol", "gpt-6.3-sol", "gpt-6.4-sol", "gpt-6.5-sol"}}
    baseline = {"OpenAI": ["gpt-6.2-sol"]}
    recorded = {"gpt-6.3-sol"}
    existing = {releases.issue_title("OpenAI", "gpt-6.4-sol")}
    assert releases.pending_models(catalogs, baseline, recorded, existing) == [("OpenAI", "gpt-6.5-sol")]
    existing.add(releases.issue_title("OpenAI", "gpt-6.5-sol"))
    assert releases.pending_models(catalogs, baseline, recorded, existing) == []



def test_publish_replay_deduplicates_closed_issues(tmp_path, monkeypatch):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"models": {"OpenAI": ["gpt-6.2-sol"]}}))
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"configurations": []}))
    monkeypatch.setattr(releases, "BASELINE", baseline)
    monkeypatch.setattr(releases, "REGISTRY", registry)
    monkeypatch.setattr(releases, "discover_catalogs", lambda: {"OpenAI": {"gpt-6.2-sol", "gpt-6.3-sol"}})
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/Trellin")
    monkeypatch.setattr(sys, "argv", ["model_releases.py", "--publish"])
    published = []

    def github_api(*args, payload=None):
        if payload is None:
            assert "state=all" in args[-1]
            return json.dumps([[{"title": issue["title"], "state": "closed"} for issue in published]])
        assert args == ("--method", "POST", "repos/example/Trellin/issues", "--input", "-")
        published.append(payload)
        return "{}"

    monkeypatch.setattr(releases, "gh", github_api)
    releases.main()
    releases.main()
    assert len(published) == 1
    assert published[0]["title"] == "Model score update — OpenAI / gpt-6.3-sol"
    assert "Pending score" in published[0]["body"]
