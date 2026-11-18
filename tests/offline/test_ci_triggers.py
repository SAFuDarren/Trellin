# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline


def test_tests_workflow_limits_push_to_main_and_keeps_pull_requests():
    workflow = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "tests.yml"
    triggers = workflow.read_text(encoding="utf-8").split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    assert triggers.splitlines() == ["  push:", "    branches: [main]", "  pull_request:"]
