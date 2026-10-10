"""The project Hypothesis profile must not be wall-clock sensitive.

Hypothesis's default per-example deadline (200ms) is a latency guard, not a
correctness assertion. Under pytest-xdist a loaded runner can push a single
example past it, turning a logic test into an intermittent ``DeadlineExceeded``
failure -- which is exactly the flakiness seen in the property-based validation
suites. ``src/tests/_conftest/hooks.py`` registers a ``plexichat`` profile that
disables the deadline suite-wide; these tests pin that so it cannot be dropped
by accident.
"""

from __future__ import annotations

import pytest

hypothesis = pytest.importorskip("hypothesis")
settings = hypothesis.settings


def test_project_profile_disables_the_deadline():
    assert settings.get_profile("plexichat").deadline is None


def test_project_profile_is_the_loaded_default(request):
    if request.config.getoption("hypothesis_profile", None):
        pytest.skip("an explicit --hypothesis-profile was passed")
    assert settings.default.deadline is None
