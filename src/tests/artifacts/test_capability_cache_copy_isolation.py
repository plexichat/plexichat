"""Regression: memoized capability verdicts must be copy-on-read/write isolated.

``get_capability`` / ``get_artifact_capabilities`` memoize ``CapabilityInfo``
objects in a process-wide cache keyed only by feature name. ``CapabilityInfo``
is a mutable dataclass with a mutable ``details`` dict, and these verdicts gate
licensed features via ``is_usable(info.state)``. If the cached instance were
handed out by reference, any caller mutating it (or its nested ``details``)
would silently rewrite the verdict used by every later request for the TTL --
the same cross-request cache-poisoning class as the bulk profile cache.

This pins the copy contract on both the cold-miss and warm-hit paths.
"""

from __future__ import annotations

import pytest

from src.core.artifacts.capabilities import (
    CapabilityState,
    clear_capability_cache,
    get_artifact_capabilities,
    get_capability,
    is_usable,
)

pytestmark = [pytest.mark.unit]

FEATURE = "plexiscribe"


@pytest.fixture(autouse=True)
def _isolated_capability_cache():
    clear_capability_cache()
    yield
    clear_capability_cache()


def test_single_feature_cold_miss_returns_independent_copy():
    first = get_capability(FEATURE)
    original_state = first.state
    original_message = first.message

    # A caller mutating the object it just computed must not touch the memo.
    first.state = CapabilityState.AVAILABLE
    first.message = "poisoned"
    first.details["injected"] = True

    second = get_capability(FEATURE)
    assert second is not first
    assert second.state == original_state
    assert second.message == original_message
    assert "injected" not in second.details
    # The gating verdict used by a later request is unchanged by the mutation.
    assert is_usable(second.state) == is_usable(original_state)


def test_single_feature_warm_hit_returns_independent_copy():
    get_capability(FEATURE)  # warm the memo
    hit = get_capability(FEATURE)
    original_state = hit.state

    hit.state = CapabilityState.AVAILABLE
    hit.details["nested"] = {"evil": True}

    again = get_capability(FEATURE)
    assert again is not hit
    assert again.state == original_state
    assert "nested" not in again.details


def test_full_catalog_returns_independent_copies():
    first_catalog = get_artifact_capabilities()
    info = first_catalog[FEATURE]
    original_state = info.state

    info.state = CapabilityState.AVAILABLE
    info.details["injected"] = True

    second_catalog = get_artifact_capabilities()
    poisoned = second_catalog[FEATURE]
    assert poisoned is not info
    assert poisoned.state == original_state
    assert "injected" not in poisoned.details
