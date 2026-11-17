"""Workspaces receive the agent guide, and researcher edits are never overwritten."""

import pytest

from research_multiplier.cli import build_parser
from research_multiplier.workflows import agent_guide

pytestmark = pytest.mark.offline


def test_install_writes_then_keeps_current_files(tmp_path):
    assert agent_guide.install(tmp_path) == {"AGENTS.md": "written", "CLAUDE.md": "written"}
    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == "@AGENTS.md\n"
    assert agent_guide.install(tmp_path) == {"AGENTS.md": "current", "CLAUDE.md": "kept"}


def test_edited_guide_is_kept(tmp_path):
    agent_guide.install(tmp_path)
    guide = tmp_path / "AGENTS.md"
    guide.write_text(guide.read_text(encoding="utf-8") + "\nMy own rule.\n", encoding="utf-8")
    assert agent_guide.install(tmp_path)["AGENTS.md"] == "kept"
    assert guide.read_text(encoding="utf-8").endswith("My own rule.\n")


def test_cli_exposes_agent_guide_and_workbench_commands():
    commands = build_parser()._subparsers._group_actions[0].choices
    assert {"agent-guide", "workbench"} <= set(commands)
