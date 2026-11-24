# SPDX-License-Identifier: Apache-2.0
"""The inbox names the bound researcher so Review never asks for a name (#328)."""

from uuid import UUID

import pytest

from research_multiplier.workflows import workbench_review

pytestmark = pytest.mark.offline
WORKSPACE = UUID("11111111-1111-4111-8111-111111111111")


class Store:
    def __init__(self, installed=True, workspace_id=WORKSPACE, user=None, error=None):
        self.installed = installed
        self.workspace_id = workspace_id
        self.user = user
        self.error = error

    def local_identity_installed(self):
        return self.installed

    def local_user(self):
        if self.error:
            raise self.error
        return self.user


def test_bound_researcher_is_named():
    store = Store(user={"id": "u", "display_name": "Fixture researcher"})
    assert workbench_review.capability(store) == {
        "review_identity": {"workspace_id": str(WORKSPACE), "display_name": "Fixture researcher"},
    }


def test_identity_without_a_resolvable_user_keeps_the_workspace_only():
    assert workbench_review.capability(Store(user=None)) == {"review_identity": {"workspace_id": str(WORKSPACE)}}
    mismatch = Store(error=ValueError("The saved research user differs from the database identity."))
    assert workbench_review.capability(mismatch) == {"review_identity": {"workspace_id": str(WORKSPACE)}}


def test_unbound_workspace_and_legacy_database():
    store = Store(workspace_id=None, user={"id": "u", "display_name": "Fixture researcher"})
    assert workbench_review.capability(store) == {
        "review_identity": {"workspace_id": None, "display_name": "Fixture researcher"},
    }
    assert workbench_review.capability(Store(installed=False)) == {}
