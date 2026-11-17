"""Record exact Formalpedia source provenance and open proof inputs locally."""


def require_enabled(store, *, role="verifier", allow_legacy=False):
    """Check the chosen workspace without migrating it or writing proof jobs."""
    version = store.query(role, """
        SELECT CASE
          WHEN to_regprocedure('rm.record_formalpedia_goal(uuid,uuid,jsonb,jsonb)') IS NOT NULL THEN 68
          WHEN to_regprocedure('rm.formalpedia_provenance(uuid)') IS NOT NULL THEN 67
        END AS version
    """)[0]["version"]
    if version == 68 or (version == 67 and allow_legacy):
        return version
    action = "inspect Formalpedia provenance" if allow_legacy else "replay Formalpedia"
    raise ValueError(f"Enable PostgreSQL migration 0068 in this research workspace to {action}.")


def record(store, statement_id, candidate, obligations):
    """Save a measured missing input without altering the frozen proof scope."""
    return store.query("verifier", "SELECT rm.record_formalpedia_application_review(%s,%s,%s) AS id",
                       statement_id, store.json(candidate), store.json(obligations))[0]["id"]


def accept(store, statement_id, lemma_id, candidate, provenance):
    """Bind an already checked local lemma to its exact source."""
    store.query("verifier", "SELECT rm.record_formalpedia_checked_lemma(%s,%s,%s,%s)",
                statement_id, lemma_id, store.json(candidate), store.json(provenance))


def accept_goal(store, statement_id, evidence_id, candidate, provenance):
    """Bind an exact whole-goal proof to its source in the receipt transaction."""
    store.query("verifier", "SELECT rm.record_formalpedia_goal(%s,%s,%s,%s)",
                statement_id, evidence_id, store.json(candidate), store.json(provenance))


def provenance(store, statement_id, *, include_goals=False):
    """Show bound and legacy source records without changing the selected workspace."""
    if require_enabled(store, role="human", allow_legacy=True) == 67:
        legacy = store.query("human", "SELECT * FROM rm.formalpedia_provenance(%s)", statement_id)
        return [{**row, "evidence_id": None, "binding": "legacy_unbound"} for row in legacy]
    return store.query("human", """
        SELECT * FROM rm.formalpedia_checked_sources(%s)
        WHERE %s OR lemma_id IS NOT NULL
    """, statement_id, include_goals)
