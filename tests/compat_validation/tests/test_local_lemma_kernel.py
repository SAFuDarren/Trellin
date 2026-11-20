"""Validate the scoped Lean proof compiler without requiring a native compiler."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from research_multiplier import general_lean, local_lemma_kernel
from research_multiplier.storage import paths
from research_multiplier.proof.lean_runner import artifact_ref


def proof(body):
    return {"format_version": 1, "proof": {"intro": {"count": 1, "body": body}}}


class ScopedLemmaTests(unittest.TestCase):
    def setUp(self):
        storage = TemporaryDirectory()
        self.addCleanup(storage.cleanup)
        workspace = paths.initialize_workspace(Path(storage.name))
        selected = paths.use_workspace(workspace)
        selected.__enter__()
        self.addCleanup(selected.__exit__, None, None, None)
        self.statement_id, self.packet_id, self.claim_id = (str(uuid4()) for _ in range(3))
        self.basis_id, self.first_id, self.second_id = (str(uuid4()) for _ in range(3))
        self.statement_sha256 = "a" * 64
        self.statement = {
            "id": self.statement_id, "packet_id": self.packet_id, "claim_id": self.claim_id,
            "statement_sha256": self.statement_sha256, "check_ready": True,
            "body": {"checker": general_lean.CHECKER, "theorem_type": "False",
                     "trusted_source": artifact_ref(general_lean._type_source("False", ["True"]).encode()),
                     "environment": {"inputs": {"driver": "b" * 64}, "sha256": "c" * 64}},
        }
        self.frozen_contract = {
            "domain": "lean-core", "sensitivity": "restricted",
            "formal_skeleton": {"format": "rm-goal-v1", "target": {"lean": {"proposition": "False"}}},
            "hypotheses": [{"label": "H1", "formal": {"lean": {"proposition": "True"}}}],
        }
        self.basis = [{"id": self.basis_id, "body": {"kind": "theorem", "formal": {
            "lean": {"declaration": "True.intro", "proposition": "True"},
        }}}]

    def query(self, _role, _sql, statement_id):
        self.assertEqual(statement_id, self.statement_id)
        return [self.statement]

    def frozen(self, claim_id):
        self.assertEqual(claim_id, self.claim_id)
        return {"contract_bytes": json.dumps(self.frozen_contract)}

    def earlier(self, identifier, proposition, submission, *, bases=(), lemmas=()):
        return {"id": identifier, "proposition": proposition, "submission": submission,
                "statement_sha256": self.statement_sha256, "basis_version_ids": list(bases),
                "local_lemma_ids": list(lemmas)}

    def test_prepares_transitive_replay_in_the_original_scope(self):
        first = self.earlier(self.first_id, "True", proof({"ref": self.basis_id}), bases=[self.basis_id])
        second = self.earlier(self.second_id, "True ∧ True", proof({"and": [
            {"apply": [{"ref": self.first_id}, {"use": 0}]}, {"use": 0},
        ]}), lemmas=[self.first_id])
        third = proof({"and": [{"apply": [{"ref": self.second_id}, {"use": 0}]}, {"use": 0}]})
        with patch.object(local_lemma_kernel.basis, "versions", return_value=self.basis):
            prepared = local_lemma_kernel.prepare(self, self.statement_id, "(True ∧ True) ∧ True",
                                                  third, [first, second])
        self.assertEqual(prepared["basis_version_ids"], [])
        self.assertEqual(prepared["local_lemma_ids"], [self.second_id])
        expected = general_lean._type_source("(True ∧ True) ∧ True", ["True"])
        self.assertEqual(prepared["trusted_source_sha256"], artifact_ref(expected.encode())["sha256"])

    def test_rejects_new_scope_stale_basis_and_foreign_lemma(self):
        first = self.earlier(self.first_id, "True", proof({"ref": self.basis_id}), bases=[self.basis_id])
        uses_first = proof({"apply": [{"ref": self.first_id}, {"use": 0}]})
        with patch.object(local_lemma_kernel.basis, "versions", return_value=self.basis):
            with self.assertRaisesRegex(ValueError, "version 1 Lean proof term"):
                local_lemma_kernel.prepare(self, self.statement_id, "True", {**uses_first, "hypotheses": ["False"]})
            with self.assertRaisesRegex(ValueError, "different reviewed statement"):
                foreign = {**first, "statement_sha256": "d" * 64}
                local_lemma_kernel.prepare(self, self.statement_id, "True", uses_first, [foreign])
        with patch.object(local_lemma_kernel.basis, "versions", return_value=[]):
            with self.assertRaisesRegex(ValueError, "unavailable or later proof input"):
                local_lemma_kernel.prepare(self, self.statement_id, "True", uses_first, [first])
