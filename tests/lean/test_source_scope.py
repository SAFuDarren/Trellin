# SPDX-License-Identifier: Apache-2.0

import json
import os
import subprocess
import sys

import pytest

from tests._native_db import disposable_project_logins


pytestmark = pytest.mark.lean


def test_source_must_match_the_frozen_proposition(tmp_path):
    workspace = tmp_path / "research"
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("RM_", "PG"))
        and not key.endswith(("_API_KEY", "_TOKEN"))
        and key not in ("PYTHONPATH", "PYTHONHOME")
    }
    env["XDG_CONFIG_HOME"] = str(tmp_path / "config")
    command = [sys.executable, "-m", "research_multiplier"]

    def cli(*args, code=0, decode=True):
        result = subprocess.run(
            [*command, *args],
            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=45,
        )
        assert result.returncode == code, f"{' '.join(args[:2])} exited with {result.returncode}"
        return json.loads(result.stdout) if decode else None

    def write_input(name, text):
        path = workspace / "inputs" / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    review = ("--actor", "Regression test", "--reason", "Approve the synthetic scope.")
    with disposable_project_logins() as logins:
        env.update(logins)
        cli("research", "init-workspace", str(workspace), decode=False)
        command.extend(("--workspace", str(workspace)))
        cli("research", "bind-workspace", decode=False)
        target_file = write_input("target.json", json.dumps({
            "title": "Reverse right identity for addition",
            "domain": "lean-core",
            "sensitivity": "restricted",
            "statement": "For every natural number n, n equals n + 0.",
            "formal": {"lean": {"proposition": "∀ n : Nat, n = n + 0"}},
        }))
        basis_file = write_input("basis.json", json.dumps({"items": [{
            "kind": "hypothesis",
            "natural": "For every natural number n, n + 0 equals n.",
            "formal": {"lean": {"proposition": "∀ n : Nat, n + 0 = n"}},
        }]}))
        decision_file = write_input("decision.json", json.dumps({
            "i1": {"action": "accept", "reason": "Reviewed the synthetic premise."},
        }))
        proof_file = write_input("Proof.lean", """import Init
namespace Regression
theorem matching (h : ∀ n : Nat, n + 0 = n) : ∀ n : Nat, n = n + 0 :=
  fun n => (h n).symm
theorem different : True := True.intro
end Regression
""")

        preview = cli("intake", "preview-target", target_file)
        target = cli("intake", "freeze-target", target_file, "--hash", preview["target_sha256"], *review)
        basis = cli("intake", "draft", basis_file, "--target", target["id"])
        cli("intake", "confirm", basis["id"], "--hash", basis["intake_sha256"], "--decisions", decision_file, *review)
        packet = cli("intake", "target", target["id"])
        statement = cli("lean", "draft-theory", packet["packet_id"])
        cli("lean", "confirm", statement["id"], "--version", str(statement["version"]),
            "--hash", statement["statement_sha256"], *review)

        check = ("lean", "check-source", statement["id"], proof_file, "--declaration")
        rejected = cli(*check, "Regression.different", code=2)
        assert not rejected["scope_matches"]
        assert rejected.get("evidence_id") is None

        accepted = cli(*check, "Regression.matching")
        assert accepted["scope_matches"] and accepted["kernel_rechecked"]
        assert any(premise["occurs_in_proof_term"] for premise in accepted["dependencies"]["premise_usage"])
        evidence = accepted["evidence_id"]
        cli("review-evidence", evidence, "approve", *review, decode=False)
        cli("promote-evidence", evidence, "--reason", "Adopt the exact verified result.", decode=False)
        assert cli("review", "packet", evidence)["claim_status"] == "proved"
