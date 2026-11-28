# SPDX-License-Identifier: Apache-2.0
"""One-time paired measurement for the workbench batching change."""

from contextvars import ContextVar
import json
from math import ceil
from pathlib import Path
from subprocess import check_output
from time import perf_counter
from types import ModuleType

import pytest

from research_multiplier.storage import paths
from research_multiplier.storage.postgres import Store
from research_multiplier.workbench import server as entry
from research_multiplier.workflows import proof_graph, workbench_sections
from tests.postgres.test_workbench_batch_reads import ROUTES, add_notes, project, running_server
from tests.postgres.test_workbench_routes import _approved_statement, _request


pytestmark = pytest.mark.postgres
BASE = "cc66da22e1ab4b593e6c65f5014a1f3ad35be94f"
WARMUPS = 3
SAMPLES = 24


def baseline():
    repo = Path(__file__).resolve().parents[2]
    parent = check_output(["git", "rev-parse", "HEAD^1"], cwd=repo, text=True).strip()
    assert parent == BASE
    modules = {}
    for name in ("proof_graph", "workbench_activity", "workbench_guidance", "workbench_sections"):
        relative = f"research_multiplier/workflows/{name}.py"
        source = check_output(["git", "show", f"{BASE}:{relative}"], cwd=repo)
        module = ModuleType(f"research_multiplier.workflows._batch_baseline_{name}")
        exec(compile(source, relative, "exec"), module.__dict__)
        modules[name] = module
    sections = modules["workbench_sections"]
    sections.proof_graph = modules["proof_graph"]
    sections.workbench_activity = modules["workbench_activity"]
    sections.workbench_guidance = modules["workbench_guidance"]
    return {workbench_sections.threads: sections.threads, proof_graph.atlas: modules["proof_graph"].atlas}


def test_pair_workbench_http_query_counts_and_p95(tmp_path, monkeypatch, pytestconfig):
    old = baseline()
    trace = ContextVar("paired_workbench_query_count", default=None)
    original_read, original_query = entry.WorkbenchServer.read, Store.query

    def query(store, role, statement, *values):
        counter = trace.get()
        if counter is not None:
            assert role == "human"
            counter[0] += 1
        return original_query(store, role, statement, *values)

    def read(server, action, *values):
        if server.compare_version == "baseline":
            action = old[action]
        counter = [0]
        token = trace.set(counter)
        try:
            return original_read(server, action, *values)
        finally:
            server.last_query_count = counter[0]
            trace.reset(token)

    monkeypatch.setattr(Store, "query", query)
    monkeypatch.setattr(entry.WorkbenchServer, "read", read)
    reports = []
    with project(tmp_path, monkeypatch) as (workspace, _), running_server(workspace) as server:
        count = 0
        for size in (1, 10, 30, 42):
            with paths.use_workspace(workspace, strict=True):
                for index in range(count, size):
                    claim, statement = _approved_statement(
                        title=f"Workload fixture {index}", premise=f"Workload fixture {index} assumes True.",
                    )
                    if index == 41:
                        continue
                    with Store() as store:
                        add_notes(store, claim, statement, f"goal:{claim}", 3)
                count = size
                with Store() as store:
                    version = store.query("human", "SELECT current_setting('server_version') AS version")[0]["version"]
            for route in ROUTES:
                elapsed = {"baseline": [], "candidate": []}
                counts = {"baseline": set(), "candidate": set()}
                for trial in range(WARMUPS + SAMPLES):
                    variants = ("baseline", "candidate") if trial % 2 == 0 else ("candidate", "baseline")
                    replies = {}
                    for variant in variants:
                        server.compare_version = variant
                        start = perf_counter()
                        replies[variant] = _request(server, "GET", route)
                        duration_ms = 1000 * (perf_counter() - start)
                        if trial >= WARMUPS:
                            elapsed[variant].append(duration_ms)
                            counts[variant].add(server.last_query_count)
                    assert replies["baseline"] == replies["candidate"]
                assert max(counts["candidate"]) <= (5 if route == "/api/threads" else 2)
                percentile = ceil(0.95 * SAMPLES) - 1
                reports.append({"claims": size, "route": route, "n": SAMPLES, "sql_calls": {
                    variant: sorted(observed) for variant, observed in counts.items()
                }, "p95_ms": {variant: round(sorted(samples)[percentile], 2) for variant, samples in elapsed.items()}})
    pytestconfig.pluginmanager.get_plugin("terminalreporter").write_line(
        "WORKBENCH_PAIRED_METRICS " + json.dumps({"postgresql": version, "warmups": WARMUPS, "results": reports}),
    )
