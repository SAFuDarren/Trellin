"""Migrations form one linear chain, so parallel branches cannot both add revision N unnoticed."""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline

VERSIONS = Path(__file__).resolve().parents[2] / "research_multiplier" / "migrations" / "versions"
HEADER = re.compile(r'^(revision|down_revision)\s*=\s*(None|"([^"]+)")', re.MULTILINE)


def revisions():
    found = {}
    for path in sorted(VERSIONS.glob("*.py")):
        fields = {name: value for name, _, value in HEADER.findall(path.read_text(encoding="utf-8"))}
        assert "revision" in fields, f"{path.name} declares no revision"
        assert fields["revision"] not in found, f"revision {fields['revision']} is declared twice ({path.name})"
        found[fields["revision"]] = (path.name, fields.get("down_revision") or None)
    return found


def test_revisions_form_a_single_linear_chain():
    found = revisions()
    children = {}
    for revision, (name, parent) in found.items():
        if parent is not None:
            assert parent in found, f"{name} points to missing down_revision {parent}"
            children.setdefault(parent, []).append(revision)
    branches = {parent: kids for parent, kids in children.items() if len(kids) > 1}
    assert not branches, f"two migrations share a parent, renumber one: {branches}"
    heads = [revision for revision in found if revision not in children]
    assert len(heads) == 1, f"expected one head, found {heads}"
    roots = [revision for revision, (_, parent) in found.items() if parent is None]
    assert len(roots) == 1, f"expected one root, found {roots}"


def test_file_names_match_numbered_revisions():
    for revision, (name, _) in revisions().items():
        if re.fullmatch(r"\d{4}", revision):
            assert name.startswith(revision + "_"), f"{name} declares revision {revision}"
