"""Regression coverage for the API fixture's wired-module teardown.

``_wire_module_graph`` sets up every core module with managers pinned to the
test's (temporary, deleted-on-teardown) database; ``_reset_wired_module_globals``
and ``_teardown_wired_modules`` are supposed to clear them again. The reset is
a hand-maintained list, so a new ``module.setup(...)`` added to the wiring
without a matching reset silently leaks a manager bound to a deleted SQLite
file into later suites (the exact order-dependence the resets exist to stop).

These tests pin the contract in two ways:

* the *source* of the two teardown helpers must cover every module
  ``_wire_module_graph`` sets up (catches a newly wired module at review time,
  before it can ever leak);
* a *runtime* check against a real ``test_client`` confirms the globals the
  helpers name are actually cleared.
"""

import importlib
import inspect
import re

from src.tests._conftest import api_fixtures

# ``.<setup>(`` on a bare module name, e.g. ``embeds.setup(db, ...)``.
_WIRED_CALL = re.compile(r"^\s*([a-z_][a-z0-9_]*)\.setup\(", re.M)
# ``name._attr`` on a bare module name, e.g. ``polls._manager = None``.
_RESET_TARGET = re.compile(r"^\s*([a-z_][a-z0-9_]*)\._", re.M)

# ``licensing.setup()`` is deliberately pinned (never reset): the API fixture
# forces the deterministic free-tier state so a developer's on-disk license
# cannot flip premium features on mid-suite. Everything else it wires must be
# cleared by the teardown helpers.
_NEVER_RESET = {"licensing"}

# Module-level attributes that hold process-global state for a wired module.
_STATE_ATTRS = (
    "_manager",
    "_db",
    "_setup_called",
    "_setup_complete",
    "_auth_mod",
    "_features",
)


def _wired_modules():
    """Core modules ``_wire_module_graph`` calls ``setup()`` on."""
    source = inspect.getsource(api_fixtures._wire_module_graph)
    return set(_WIRED_CALL.findall(source))


def _reset_modules():
    """Core modules the teardown helpers clear."""
    source = inspect.getsource(
        api_fixtures._reset_wired_module_globals
    ) + inspect.getsource(api_fixtures._teardown_wired_modules)
    return set(_RESET_TARGET.findall(source))


def _leftover_state(module):
    return {
        attribute: getattr(module, attribute)
        for attribute in _STATE_ATTRS
        if hasattr(module, attribute) and getattr(module, attribute)
    }


def test_reset_helpers_cover_every_wired_module():
    wired = _wired_modules() - _NEVER_RESET
    reset = _reset_modules()
    assert wired, "failed to parse any module.setup() call out of _wire_module_graph"
    assert wired <= reset, (
        "modules wired by _wire_module_graph but not cleared on teardown: "
        f"{sorted(wired - reset)}"
    )
    assert reset <= wired, (
        "modules cleared on teardown but not wired by _wire_module_graph: "
        f"{sorted(reset - wired)}"
    )


def test_reset_helpers_clear_the_wired_graph(test_client):
    """A real wired graph must be fully cleared by the teardown helpers."""
    wired = _wired_modules() - _NEVER_RESET
    modules = {name: importlib.import_module(f"src.core.{name}") for name in wired}

    # The API fixture has wired the graph, so at least some state is live.
    assert any(_leftover_state(module) for module in modules.values()), (
        "expected the wired module graph to hold state before teardown"
    )

    api_fixtures._teardown_wired_modules()

    leftovers = {
        name: state
        for name, module in modules.items()
        if (state := _leftover_state(module))
    }
    assert not leftovers, f"wired module globals survived teardown: {leftovers}"
