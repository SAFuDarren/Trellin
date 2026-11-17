"""Persistence operations for the scheduler's signed Lean passes."""

from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID

from research_multiplier.policy.contracts import ArtifactRef
from research_multiplier.storage.postgres import DatabaseOperationError, Store


@dataclass(frozen=True)
class PendingLeanPass:
    policy_id: UUID
    statement_id: UUID
    model_rounds: int
    inputs_sha256: str


def pending_lean_passes(store: Store, limit: int, policy_ids) -> list[PendingLeanPass]:
    rows = store.query("scheduler", "SELECT * FROM rm.lean_progress_queue(%s,%s::uuid[])", limit, policy_ids)
    return [PendingLeanPass(row["id"], row["statement_id"], row["model_rounds"], row["inputs_sha256"]) for row in rows]


@dataclass(frozen=True)
class LeanPassSettings:
    private_model: dict | None
    route_parallelism: int
    route_stall_limit: int


class ClaimedLeanPass:
    """Read and finish a pass over the same PostgreSQL session that claimed it."""

    def __init__(self, store: Store, pending: PendingLeanPass):
        self._store = store
        self._pending = pending

    def settings(self) -> LeanPassSettings:
        row, = self._store.query("scheduler", """
            SELECT policy #> '{lean,private_model}' AS private_model,
                   coalesce((policy #>> '{lean,route_parallelism}')::integer, 1) AS route_parallelism,
                   coalesce((policy #>> '{lean,route_stall_limit}')::integer, 3) AS route_stall_limit
            FROM rm.research_progress_status WHERE id = %s
        """, self._pending.policy_id)
        return LeanPassSettings(row["private_model"], row["route_parallelism"], row["route_stall_limit"])

    def is_active(self) -> bool:
        return bool(self._store.query("scheduler", """
            SELECT 1 FROM rm.research_progress_status WHERE id = %s AND state = 'awaiting_evidence'
        """, self._pending.policy_id))

    def continuation(self) -> ArtifactRef | None:
        row, = self._store.query("scheduler", "SELECT rm.lean_progress_continuation(%s,%s) AS continuation",
                                 self._pending.policy_id, self._pending.inputs_sha256)
        return row["continuation"]

    def finish(self, report: dict) -> None:
        self._store.query("scheduler", "SELECT rm.finish_lean_progress(%s,%s,%s)",
                          self._pending.policy_id, self._pending.inputs_sha256, self._store.json(report))


@contextmanager
def hold_lean_pass(store: Store, pending: PendingLeanPass):
    """Pin the caller's scheduler connection until the pass completes or unwinds."""
    row, = store.query("scheduler", "SELECT rm.lock_lean_progress(%s,%s) AS acquired",
                       pending.policy_id, pending.inputs_sha256)
    if not row["acquired"]:
        yield None
        return
    try:
        yield ClaimedLeanPass(store, pending)
    finally:
        try:
            store.query("scheduler", "SELECT rm.unlock_lean_progress(%s)", pending.policy_id)
        except DatabaseOperationError as error:
            if not error.retryable:
                raise
            # Closing the caller's Store releases the lock with the old session.
