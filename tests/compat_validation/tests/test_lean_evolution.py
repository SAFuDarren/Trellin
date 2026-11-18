import copy
import unittest

from research_multiplier.evaluations.lean_evolution import CAMPAIGN, _gate


def attempt(outcome, calls):
    kind = {"verified": "lean", "refuted": "counterexample"}.get(outcome)
    return {"result": {"outcome": outcome, "search_status": "proof_search_blocked" if not kind else
                       "awaiting_evidence_review",
                       "receipts": [{"kind": kind, "review_status": "pending", "report": {"sha256": "a" * 64}}]
                       if kind else [], "model": {"reservation_count": calls}}}


def comparison():
    report = {"kind": CAMPAIGN, "split": "holdout", "source_unchanged": True, "cases": [
        {"id": "proof", "order": ["baseline", "candidate"], "steps": [
            {"statement_id": "goal-1", "expected": "verified",
             "sides": {"baseline": attempt("no_result", 2), "candidate": attempt("verified", 2)}}]},
        {"id": "refutation", "order": ["candidate", "baseline"], "steps": [
            {"statement_id": "goal-2", "expected": "refuted",
             "sides": {"baseline": attempt("refuted", 1), "candidate": attempt("refuted", 1)}}]},
    ]}
    review = {"format_version": 1, "cases": [
        {"case_id": key, "baseline_minutes": "1", "candidate_minutes": "0.5",
         "baseline_invalid_receipts": 0, "candidate_invalid_receipts": 0}
        for key in ("proof", "refutation")
    ]}
    return report, review


class LeanEvolutionTests(unittest.TestCase):
    def test_accepts_more_exact_progress_after_measuring_review_for_every_case(self):
        report, review = comparison()
        summary = _gate(report, review)
        self.assertEqual(summary["candidate"], {"exact": 2, "calls": 3, "review_minutes": "1.0"})
        review["cases"].pop()
        with self.assertRaisesRegex(ValueError, "exactly once"):
            _gate(report, review)

    def test_disqualifies_changed_source_wrong_scope_and_lost_baseline_closure(self):
        report, review = comparison()
        changed = copy.deepcopy(report)
        changed["source_unchanged"] = False
        with self.assertRaisesRegex(ValueError, "unchanged source"):
            _gate(changed, review)
        bad = copy.deepcopy(report)
        bad["cases"][1]["steps"][0]["sides"]["candidate"] = attempt("verified", 1)
        with self.assertRaisesRegex(ValueError, "unexpected exact evidence"):
            _gate(bad, review)
        report["cases"][1]["steps"][0]["sides"]["candidate"] = attempt("no_result", 1)
        with self.assertRaisesRegex(ValueError, "lost an exact baseline"):
            _gate(report, review)
