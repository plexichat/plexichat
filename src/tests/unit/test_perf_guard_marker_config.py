"""Keep the performance guards opt-in and separately runnable in CI.

The main suite deselects ``performance`` in ``pytest.ini``; these tests are
marked with it so a normal run never pays their cost.  This module asserts the
wiring stays that way: the marker is registered, ``--strict-markers`` is on,
the default ``-m`` expression still excludes ``performance``, and every
``test_perf_guard_*.py`` file keeps the marker so none of them silently leak
into the default run.

Run the guards explicitly with:

    pytest -m performance -n 0 src/tests/unit/test_perf_guard_*.py

Run with: pytest -m performance -n 0 src/tests/unit/test_perf_guard_marker_config.py
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.performance

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PYTEST_INI = _REPO_ROOT / "pytest.ini"
_GUARD_DIR = Path(__file__).resolve().parent


def _ini_text():
    assert _PYTEST_INI.is_file(), f"missing pytest.ini at {_PYTEST_INI}"
    return _PYTEST_INI.read_text(encoding="utf-8")


def test_performance_marker_is_registered(pytestconfig):
    """The marker these guards rely on must be known to pytest."""
    registered = [line.split(":", 1)[0].strip() for line in pytestconfig.getini("markers")]
    assert "performance" in registered, "the performance marker is not registered"


def test_strict_markers_enabled():
    text = _ini_text()
    assert "--strict-markers" in text, "pytest must reject unregistered markers"


def test_default_run_deselects_performance():
    """A plain pytest run must not execute the performance guards."""
    text = _ini_text()
    deselect_lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith('-m "')
    ]
    assert deselect_lines, "no default -m deselection expression found"
    assert any("not performance" in line for line in deselect_lines), (
        "the default -m expression no longer excludes performance tests"
    )


def test_every_perf_guard_file_is_marked():
    """None of these guards may accidentally join the default suite."""
    offenders = []
    for path in sorted(_GUARD_DIR.glob("test_perf_guard_*.py")):
        if "pytest.mark.performance" not in path.read_text(encoding="utf-8"):
            offenders.append(path.name)
    assert not offenders, f"unmarked perf guard files: {offenders}"
