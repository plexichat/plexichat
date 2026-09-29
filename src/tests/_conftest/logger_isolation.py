"""Isolation for tests that call ``utils.logger.setup()``.

``logger.setup()`` mutates the process-global ``"AppLogger"`` logger: it
replaces the level, handlers and filters, and rebinds the module-level
``_logger_instance``. A module- or session-scoped test fixture that calls it
for its own scope therefore leaks the configured level into every later test
that runs in the same worker. The failure mode is silent and order-dependent:
a fixture raising the level to ``ERROR`` makes a ``caplog``-based ``WARNING``
assertion in a *different* file observe nothing, because ``pytest-xdist``'s
``--dist loadfile`` hands files to workers in arbitrary order.

``isolated_logger()`` makes such a fixture symmetrical: it calls
``logger.setup()`` on entry and restores the previous level and propagation
flag on exit.

``_app_logger_level_guard`` is the suite-wide net. It is an autouse fixture
that runs around every test and fails when the live ``AppLogger`` level does
not match what a currently-active ``isolated_logger()`` scope expects (or the
session baseline, when no scope is active). A fixture that forgets to restore
therefore becomes an immediate, named failure instead of a confusing assertion
error somewhere else entirely.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any, Iterator

import pytest

import utils.logger as logger

__all__ = ["isolated_logger", "_app_logger_level_guard"]

# Level configured for the suite at import time by ``config_env.py``. Read here
# (rather than from a session fixture) because session-scoped fixtures may
# themselves change the level, and this module is imported right after
# ``config_env`` in the root conftest.
_BASELINE_LEVEL: int = logging.getLogger("AppLogger").level

# Mirrors the string-to-level map in ``utils.logger.setup`` so the scope
# records exactly the level ``setup`` will apply.
_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

# Levels set by the currently-active ``isolated_logger()`` scopes, innermost
# last. Empty when no test has intentionally reconfigured the logger.
_active_levels: "list[int]" = []


def _resolve_level(level: Any) -> int:
    if isinstance(level, int):
        return level
    return _LEVELS.get(str(level).upper(), logging.INFO)


def expected_level() -> int:
    """Return the level a correctly-behaving suite should currently have."""
    if _active_levels:
        return _active_levels[-1]
    return _BASELINE_LEVEL


@contextlib.contextmanager
def isolated_logger(
    *, suppress_setup_errors: bool = False, **setup_kwargs: Any
) -> Iterator[None]:
    """Call ``logger.setup()`` and restore the prior level on exit.

    Extra keyword arguments are forwarded verbatim to ``utils.logger.setup``.
    Pass ``suppress_setup_errors=True`` to preserve callers that historically
    wrapped ``setup()`` in a bare ``except Exception: pass`` (the logger is
    best-effort for tests).
    """
    app_logger = logging.getLogger("AppLogger")
    previous_level = app_logger.level
    previous_propagate = app_logger.propagate
    _active_levels.append(_resolve_level(setup_kwargs.get("level")))
    try:
        try:
            logger.setup(**setup_kwargs)
        except Exception:
            if not suppress_setup_errors:
                raise
        yield
    finally:
        _active_levels.pop()
        app_logger.setLevel(previous_level)
        app_logger.propagate = previous_propagate


def _report_leak(
    request: pytest.FixtureRequest, actual: int, expected: int, when: str
) -> None:
    pytest.fail(
        "AppLogger level leaked: it is {actual!r} at the {when} of {node!r}, but "
        "{expected!r} was expected. A previous test or fixture called "
        "logger.setup() without restoring the process-global level; wrap that "
        "call in src.tests._conftest.logger_isolation.isolated_logger().".format(
            actual=actual,
            when=when,
            node=request.node.nodeid,
            expected=expected,
        ),
        pytrace=False,
    )


@pytest.fixture(autouse=True)
def _app_logger_level_guard(request: pytest.FixtureRequest) -> Iterator[None]:
    """Fail fast when a test leaves the global ``AppLogger`` level changed.

    The expected level is recomputed at teardown so that an active
    ``isolated_logger()`` scope is honoured in both setup and reverse-teardown
    order relative to this fixture.
    """
    app_logger = logging.getLogger("AppLogger")
    expected = expected_level()
    if app_logger.level != expected:
        _report_leak(request, app_logger.level, expected, "start")
    yield
    expected = expected_level()
    if app_logger.level != expected:
        _report_leak(request, app_logger.level, expected, "end")
