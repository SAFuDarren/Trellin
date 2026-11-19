from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
from threading import Event

import pytest

from research_multiplier.workflows import release_updates


pytestmark = pytest.mark.offline


@pytest.fixture
def release_source(monkeypatch):
    response = {"status": 200, "body": {"draft": False, "prerelease": False, "tag_name": "v1.10.0"}}
    calls = []

    class Connection:
        def __init__(self, host, *, timeout):
            assert host == "api.github.com" and timeout > 0

        def request(self, method, path, *, headers):
            assert method == "GET" and path == "/repos/SAFuDarren/Trellin/releases/latest"
            assert headers == {"Accept": "application/vnd.github+json", "User-Agent": "Trellin-Release-Check"}
            calls.append(path)
            if "exception" in response:
                raise response["exception"]

        def getresponse(self):
            result = BytesIO(json.dumps(response["body"]).encode())
            result.status = response["status"]
            return result

        def close(self):
            pass

    monkeypatch.setattr(release_updates, "HTTPSConnection", Connection)
    return response, calls


def test_opt_in_cooldown_and_dismissal_are_shared_across_workspaces(tmp_path, release_source):
    response, calls = release_source
    now = [10_000]
    preferences = tmp_path / "config" / "release-updates.sqlite3"

    def workspace(version="1.9.0"):
        return release_updates.ReleaseUpdates(preferences, installed_version=lambda: version, clock=lambda: now[0])

    first = workspace()
    assert first.snapshot() == {
        "installed_version": "1.9.0", "automatic": False, "last_checked_at": None,
        "status": "not_checked", "available": None,
    }
    assert first.apply({"action": "auto_check"}) == first.snapshot()
    assert not calls and not preferences.exists()
    first.apply({"action": "automatic", "enabled": True})
    assert workspace().snapshot()["automatic"] is True
    assert not calls

    found = workspace().apply({"action": "auto_check"})
    assert found["available"] == {
        "version": "1.10.0", "url": "https://github.com/SAFuDarren/Trellin/releases/tag/v1.10.0", "dismissed": False,
    }
    first.apply({"action": "dismiss", "version": "1.10.0"})
    assert workspace().snapshot()["available"]["dismissed"] is True
    assert workspace("1.10.0").snapshot()["status"] == "current"

    now[0] += 86_399
    workspace().apply({"action": "auto_check"})
    assert len(calls) == 1
    now[0] += 1
    response["body"]["tag_name"] = "v1.10.1"
    fresh = workspace().apply({"action": "auto_check"})
    assert fresh["available"]["version"] == "1.10.1"
    assert fresh["available"]["dismissed"] is False
    assert fresh["last_checked_at"] == now[0]
    workspace().apply({"action": "automatic", "enabled": False})
    now[0] += 86_400
    first.apply({"action": "auto_check"})
    assert len(calls) == 2
    first.apply({"action": "check"})
    assert len(calls) == 3


@pytest.mark.parametrize(("changed", "expected"), [
    ({"body": {"draft": False, "prerelease": False, "tag_name": "v1.2.0"}}, "current"),
    ({"body": {"draft": True, "prerelease": False, "tag_name": "v1.13.0"}}, "no_release"),
    ({"body": {"draft": False, "prerelease": True, "tag_name": "v1.13.0"}}, "no_release"),
    ({"body": {"draft": False, "prerelease": False, "tag_name": "v1.13.0-rc.1"}}, "unavailable"),
    ({"body": {"draft": False, "prerelease": False, "tag_name": "v1.13.0/another-path"}}, "unavailable"),
    ({"status": 404}, "no_release"),
    ({"status": 302}, "unavailable"),
    ({"status": 403}, "unavailable"),
    ({"status": 429}, "unavailable"),
    ({"exception": TimeoutError("network failure")}, "unavailable"),
    ({"exception": OSError("offline")}, "unavailable"),
])
def test_release_source_outcomes_and_failure_cooldown(tmp_path, release_source, changed, expected):
    response, calls = release_source
    response.update(changed)
    updates = release_updates.ReleaseUpdates(tmp_path / "settings", installed_version=lambda: "1.9.0")
    updates.apply({"action": "automatic", "enabled": True})
    checked = updates.apply({"action": "auto_check"})
    assert checked["status"] == expected and checked["available"] is None
    assert updates.apply({"action": "auto_check"}) == checked
    assert len(calls) == 1


def test_checkout_and_browser_open_do_not_contact_the_release_source(tmp_path, release_source):
    _, calls = release_source
    updates = release_updates.ReleaseUpdates(tmp_path / "settings", installed_version=lambda: None)
    assert updates.snapshot()["installed_version"] is None
    assert updates.apply({"action": "check"})["status"] == "unavailable"
    assert not calls


def test_two_workbench_servers_only_reserve_one_automatic_check(tmp_path, monkeypatch):
    settings = tmp_path / "settings"
    first = release_updates.ReleaseUpdates(settings, installed_version=lambda: "1.9.0")
    second = release_updates.ReleaseUpdates(settings, installed_version=lambda: "1.9.0")
    started = Event()
    finish = Event()

    def slow_release():
        assert not started.is_set()
        started.set()
        assert finish.wait(5)
        return "release", "v1.10.0"

    monkeypatch.setattr(release_updates, "_latest_release", slow_release)
    first.apply({"action": "automatic", "enabled": True})
    with ThreadPoolExecutor() as pool:
        ongoing = pool.submit(first.apply, {"action": "auto_check"})
        try:
            assert started.wait(5)
            assert second.apply({"action": "auto_check"})["status"] == "not_checked"
        finally:
            finish.set()
        assert ongoing.result()["status"] == "available"
