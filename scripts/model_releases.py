# SPDX-License-Identifier: Apache-2.0
"""Check supported model catalogs and open pending score requests on GitHub."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / ".github" / "model-catalog-baseline.json"
REGISTRY = ROOT / ".github" / "agent-models.json"
CATALOGS = {
    "OpenAI": "https://developers.openai.com/api/docs/models.md",
    "Anthropic": "https://platform.claude.com/docs/en/models/overview.md",
    "Google": "https://aistudio.google.com/docs/models.md",
}
TITLE_PREFIX = "Model score update — "
MAX_CATALOG_BYTES = 1_000_000


def model_ids(provider: str, catalog: str) -> set[str]:
    if provider == "OpenAI":
        models = re.findall(r"\(/api/docs/models/(gpt-\d[.\d]*(?:-[a-z\d]+)*?)\.md\)", catalog)
        return {model for model in models if "cyber" not in model.split("-")}
    if provider == "Anthropic":
        row = re.search(r"^\|\s*Claude API ID\s*\|([^\n]+)", catalog, re.MULTILINE)
        return set(re.findall(r"`(claude-(?:fable|opus|sonnet|haiku)-[a-z\d-]+)`", row[1])) if row else set()
    models = re.findall(r"\*Model code\*\s+\*\*(gemini-[\d.]+(?:-[a-z\d]+)+)\*\*", catalog)
    return {model for model in models if not {"audio", "image", "live", "tts"} & set(model.split("-"))}


def discover_catalogs() -> dict[str, set[str]]:
    catalogs = {}
    for provider, url in CATALOGS.items():
        response = subprocess.run(
            ["curl", "-fLsS", "--proto", "=https", "--proto-redir", "=https",
             "--max-time", "15", "--max-filesize", str(MAX_CATALOG_BYTES), url],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
        ids = model_ids(provider, response.stdout)
        if not ids:
            raise SystemExit(f"{provider} catalog returned no supported model IDs. Check its format.")
        catalogs[provider] = ids
    return catalogs


def pending_models(
    catalogs: dict[str, set[str]], baseline: dict[str, list[str]], scored_ids: set[str], existing_titles: set[str]
) -> list[tuple[str, str]]:
    return [
        (provider, model)
        for provider, ids in catalogs.items()
        for model in sorted(ids - set(baseline[provider]) - scored_ids)
        if issue_title(provider, model) not in existing_titles
    ]


def issue_title(provider: str, model: str) -> str:
    return f"{TITLE_PREFIX}{provider} / {model}"


def gh(*args: str, payload: dict | None = None) -> str:
    return subprocess.run(
        ["gh", "api", *args],
        input=json.dumps(payload) if payload is not None else None,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    ).stdout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true", help="Create pending score issues. Default prints candidates.")
    args = parser.parse_args()
    catalogs = discover_catalogs()
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))["models"]
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    recorded = {configuration["model"] for configuration in registry["configurations"]}
    repository = os.environ.get("GITHUB_REPOSITORY")
    existing = set()
    if repository:
        query = f"repos/{repository}/issues?state=all&labels=type%3Achore&per_page=100"
        pages = json.loads(gh("--paginate", "--slurp", query))
        existing = {item["title"] for page in pages for item in page if "pull_request" not in item}
    if args.publish and not repository:
        raise SystemExit("Set GITHUB_REPOSITORY before publishing issues.")
    for provider, model in pending_models(catalogs, baseline, recorded, existing):
        title = issue_title(provider, model)
        print(title, flush=True)
        if args.publish:
            body = (
                f"**Status:** Pending score\n\n"
                f"**Model:** `{model}`\n\n"
                f"Listed in the [official {provider} model catalog]({CATALOGS[provider]}). "
                "Add the exact runner, model and effort to the registry after a comparable "
                "Artificial Analysis score becomes available. Continue using published registry scores."
            )
            gh(
                "--method", "POST", f"repos/{repository}/issues", "--input", "-",
                payload={"title": title, "body": body, "labels": ["type:chore", "status:triage"]},
            )


if __name__ == "__main__":
    main()
