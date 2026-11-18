"""The compatibility suite must use a standalone wheel environment."""

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.offline

_RUNNER = Path(__file__).resolve().parents[1] / "compat_validation" / "run_regressions.py"
_BUNDLE = Path("Trellin.app") / "Contents" / "Resources" / "python"


@pytest.fixture
def runner(monkeypatch):
    spec = importlib.util.spec_from_file_location("compat_regression_runner", _RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "argv", [str(_RUNNER), "offline"])
    return module


def test_bundled_interpreter_is_rejected_before_inspecting_metadata(runner, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "executable", str(tmp_path / _BUNDLE / "bin" / "python"))
    monkeypatch.setattr(runner, "distribution", lambda _: pytest.fail("Metadata inspected inside Trellin.app"))
    with pytest.raises(SystemExit) as rejected:
        runner.main()
    assert rejected.value.code == 2
    assert "separate wheel environment" in capsys.readouterr().err


def test_bundled_package_is_rejected_from_an_external_interpreter(runner, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "executable", str(tmp_path / "venv" / "bin" / "python"))
    version = (_RUNNER.parent / "PACKAGE_VERSION").read_text(encoding="ascii").strip()
    package = tmp_path / _BUNDLE / "lib" / "site-packages"
    installed = SimpleNamespace(version=version, locate_file=lambda file: package / file)
    monkeypatch.setattr(runner, "distribution", lambda _: installed)
    with pytest.raises(SystemExit) as rejected:
        runner.main()
    assert rejected.value.code == 2
    assert "separate wheel environment" in capsys.readouterr().err
