"""Regression: the full capability catalog reuses the bounded per-feature LRU.

``get_artifact_capabilities()`` is called by both the launcher and admin
catalog routes (and both payload caches are warmed at boot). Re-running every
evaluator for each of those builds repeated the voice-transcription provider
import and backend probe. The full map now reuses the same bounded, short-TTL
LRU that ``get_capability()`` fills, so a second build within the TTL is made
of cache hits instead of fresh evaluations.

The cache holds only global, user-independent config/license verdicts. No user
id is part of the key, so one viewer's catalog can never leak into another's;
a miss is always recomputed from the live config/license (fail-closed).
"""

import pytest

import src.core.artifacts.capabilities as caps


@pytest.fixture
def counted_evaluators(monkeypatch):
    """Wrap every evaluator with a per-feature call counter."""
    caps.clear_capability_cache()
    real = caps._evaluators()
    calls = {}

    def counting_registry():
        def wrap(name, evaluator):
            def counted(artifacts):
                calls[name] = calls.get(name, 0) + 1
                return evaluator(artifacts)

            return counted

        return {name: wrap(name, evaluator) for name, evaluator in real.items()}

    monkeypatch.setattr(caps, "_evaluators", counting_registry)
    yield calls
    caps.clear_capability_cache()


def test_full_catalog_reuses_bounded_feature_cache(counted_evaluators):
    """Two full builds inside the TTL evaluate each feature only once."""
    first = caps.get_artifact_capabilities()
    assert set(first) == set(counted_evaluators)
    assert all(count == 1 for count in counted_evaluators.values())

    second = caps.get_artifact_capabilities()
    assert second == first
    # Before the fix every evaluator ran again on this call (counts == 2).
    assert all(count == 1 for count in counted_evaluators.values())


def test_catalog_and_single_feature_share_the_same_cache(counted_evaluators):
    """A previously memoized single-feature verdict is reused by the catalog."""
    single = caps.get_capability("artifacts")
    assert counted_evaluators["artifacts"] == 1

    catalog = caps.get_artifact_capabilities()
    assert catalog["artifacts"] == single
    assert counted_evaluators["artifacts"] == 1


def test_explicit_config_bypasses_cache(counted_evaluators):
    """An explicit config still forces a fresh evaluation of every feature."""
    caps.get_artifact_capabilities()
    before = dict(counted_evaluators)

    caps.get_artifact_capabilities({})
    assert all(
        counted_evaluators[name] == before[name] + 1 for name in counted_evaluators
    )


def test_clear_forces_fresh_evaluation(counted_evaluators):
    """The authoritative invalidation hook drops every cached verdict."""
    caps.get_artifact_capabilities()
    caps.get_artifact_capabilities()
    assert all(count == 1 for count in counted_evaluators.values())

    caps.clear_capability_cache()
    caps.get_artifact_capabilities()
    assert all(count == 2 for count in counted_evaluators.values())
