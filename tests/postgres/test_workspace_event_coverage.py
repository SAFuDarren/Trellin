# SPDX-License-Identifier: Apache-2.0
"""Check event coverage at the shared PostgreSQL write boundary."""

import re

import pytest

from research_multiplier.storage.migrate import upgrade
from tests._native_db import disposable_database


pytestmark = pytest.mark.postgres

# These writes either support another captured change in the same transaction
# or belong to project setup/the event mechanism itself.
SUPPORTING_RELATIONS = {
    "claim_evidence_revisions": "evidence freshness fence",
    "claim_node_numbers": "assigned from the captured proof node source",
    "claim_reference_aliases": "captured on the canonical claim code change",
    "database_identity": "initial workspace binding, before a subscriber exists",
    "exploration_goal_links": "written with the captured child packet",
    "formal_item_keys": "version allocation alongside formal_item_versions",
    "formalization_reviews": "review gate revision alongside packet and clarification records",
    "foundation_revision": "foundation freshness fence",
    "harness_components": "migration-owned verifier registration",
    "job_cancellations": "attribution for the captured job status change",
    "lean_search_call_slots": "reservation alongside the captured search budget",
    "legacy_lean_review_revisions": "review ordering with the captured Lean review",
    "local_lean_lemma_basis": "written with the captured lemma",
    "local_lean_lemma_dependencies": "written with the captured lemma",
    "local_review_requests": "idempotency index alongside the captured review",
    "local_users": "workspace initialization and restore",
    "novelty_review_originals": "written with the captured novelty review",
    "novelty_review_searches": "written with the captured novelty review",
    "packet_basis_items": "written with the captured packet approval",
    "production_checkers": "migration-owned domain capability registration",
    "project_model_months": "rollup with the captured dispatch reservation",
    "research_goal_basis_bindings": "written with the captured selection and packet",
    "research_plan_dependencies": "written with the captured plan",
    "route_condition_disposition_fences": "concurrency fence alongside a condition resolution",
    "workspace_event_heads": "event sequence state",
    "workspace_events": "event log itself",
}

SOURCE_WRITE = re.compile(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+(?:rm\.)?([a-z][a-z_0-9]*)", re.I)
# PostgreSQL's catalog bits for INSERT, DELETE and UPDATE.
OPERATIONS = {"insert": 4, "delete": 8, "update": 16}
SCOPE_FIELDS = {
    "attempt": "attempt_id",
    "check": "check_id",
    "claim": "claim_id",
    "clarification": "request_id",
    "condition": "request_id",
    "evidence": "evidence_id",
    "goal": "goal_id",
    "guidance": "guidance_id",
    "packet": "packet_id",
    "policy": "policy_id",
    "statement": "statement_id",
    "subject": "id",
}

SUPPORTING_WRITES = {
    # Allocation is invoked by the claim's source trigger. The code-change
    # trigger follows subsequent renames and skips allocations of node numbers.
    ("ensure_claim_reference", "insert", "claim_references"),
}

BOOTSTRAP_ENTRYPOINTS = {"bind_research_workspace"}
DELEGATED_WRITES = {
    "choose_exploration": ("propose_formalization", ("insert", "formalization_packets")),
    "review_evidence_as": ("review_evidence", ("insert", "decisions")),
}


def test_workspace_writer_relations_have_event_coverage_or_a_supporting_record():
    import psycopg

    with disposable_database() as dsn:
        upgrade(dsn)
        with psycopg.connect(dsn) as connection:
            tables = {name for name, in connection.execute("""
                SELECT c.relname FROM pg_class c JOIN pg_namespace n ON c.relnamespace = n.oid
                WHERE n.nspname = 'rm' AND c.relkind IN ('r', 'p')
            """)}
            captures = {}
            operations = {}
            uuid_columns = set(connection.execute("""
                SELECT c.relname, a.attname FROM pg_class c JOIN pg_namespace n ON c.relnamespace = n.oid
                JOIN pg_attribute a ON a.attrelid = c.oid
                WHERE n.nspname = 'rm' AND a.atttypid = 'uuid'::regtype AND NOT a.attisdropped
            """))
            for name, function, enabled, deferred, flags, arguments in connection.execute("""
                SELECT target.relname, handler.proname, t.tgenabled,
                       t.tgdeferrable AND t.tginitdeferred, t.tgtype::integer, t.tgargs
                FROM pg_trigger t JOIN pg_class target ON t.tgrelid = target.oid
                JOIN pg_namespace n ON target.relnamespace = n.oid
                JOIN pg_proc handler ON t.tgfoid = handler.oid
                WHERE n.nspname = 'rm' AND NOT t.tgisinternal AND t.tgname LIKE 'publish_%'
            """):
                assert enabled in ("O", "A") and deferred, (name, function)
                captures.setdefault(name, set()).add(function)
                operations[name] = operations.get(name, 0) | flags
                if function == "capture_workspace_change_event":
                    _, scope, _ = arguments.decode("ascii").split("\0")
                    if scope:
                        assert (name, SCOPE_FIELDS[scope]) in uuid_columns
            assert not (captures.keys() & SUPPORTING_RELATIONS.keys())
            assert tables == captures.keys() | SUPPORTING_RELATIONS.keys()
            assert captures["workbench_guidance"] == {"capture_workspace_change_event"}
            assert captures["clarification_resolutions"] == {"capture_workspace_change_event"}

            # Enumerate the installed SQL writers, including private functions
            # underneath CLI, HTTP and worker entry points.
            writers = {}
            support_only_entrypoints = {}
            missing_triggers = set()
            for name, definition, granted in connection.execute("""
                SELECT fn.proname, pg_get_functiondef(fn.oid),
                       fn.prosecdef AND EXISTS (
                           SELECT FROM unnest(ARRAY['rm_human','rm_agent','rm_scheduler','rm_verifier']) AS role(name)
                           WHERE has_function_privilege(role.name, fn.oid, 'execute')
                       )
                FROM pg_proc fn JOIN pg_namespace n ON fn.pronamespace = n.oid
                WHERE n.nspname = 'rm' AND fn.prokind = 'f'
            """):
                changes = {(verb.split()[0].lower(), table.lower()) for verb, table in SOURCE_WRITE.findall(definition)
                           if table.lower() in tables}
                if not changes:
                    continue
                writers.setdefault(name, set()).update(changes)
                if granted and not any(table in captures for _, table in changes):
                    support_only_entrypoints[name] = definition
                missing_triggers.update((name, verb, table) for verb, table in changes
                                        if table in captures and not operations[table] & OPERATIONS[verb])
            assert {
                "add_workbench_guidance", "record_workbench_guidance_submission",
                "request_clarification", "resolve_clarification", "review_evidence",
                "register_flow_derivations", "retire_formal_item", "finish_lean_progress", "submit_attempt",
            } <= writers.keys()
            assert missing_triggers == SUPPORTING_WRITES
            assert support_only_entrypoints.keys() == BOOTSTRAP_ENTRYPOINTS | DELEGATED_WRITES.keys()
            for name, (writer, change) in DELEGATED_WRITES.items():
                assert re.search(rf"\b{writer}\s*\(", support_only_entrypoints[name])
                assert change in writers[writer]
