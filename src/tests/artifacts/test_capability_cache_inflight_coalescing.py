"""Concurrent cold misses of the bounded capability cache must coalesce.

``get_capability(feature)`` is a hot path: Suite/artifact gates call it on every
request. With ``config=None`` the verdict is memoized in a bounded LRU
(``_capability_cache``) whose miss re-runs the authoritative evaluator. For
``voice_transcription`` that evaluator imports the transcription provider and
probes backend availability -- work that must happen once per cold miss, not
once per in-flight request.

Before the fix, N threads cold-missing the same feature each ran the expensive
evaluator independently (a thundering herd after startup, a TTL rollover, or a
``clear_capability_cache()`` license hot-swap). This module measures that with a
concurrency call-count test: N concurrent misses -> N recomputes before, ~1
after. It also proves the coalescing is per-key (different features never share
a verdict) and that a full invalidation re-arms the authoritative recompute so
a stale "available" verdict can never be resurrected.

No app, DB, config or network is required.
"""

from __future__ import annotations

import threading
import time

import pytest

from src.core.artifacts import capabilities as caps
from src.core.artifacts.capabilities import CapabilityInfo, CapabilityState

pytestmark = [pytest.mark.unit]

_N_CALLERS = 16
_FEATURE = "voice_transcription"


@pytest.fixture(autouse=True)
def _clean_caches():
    caps.clear_capability_cache()
    yield
    caps.clear_capability_cache()


def _verdict(state: CapabilityState, tag: str) -> CapabilityInfo:
    return CapabilityInfo(
        feature=_FEATURE,
        state=state,
        message="probe verdict",
        details={"tag": tag},
    )


def test_concurrent_cold_misses_coalesce_to_one_recompute(monkeypatch) -> None:
    """16 concurrent cold misses must pay for one authoritative evaluation."""
    real = caps._evaluators

    def evaluators_for_target():
        base = real()
        base[_FEATURE] = _slow_evaluator
        return base

    calls = {"n": 0}
    eval_started = threading.Event()
    release = threading.Event()
    start = threading.Barrier(_N_CALLERS)
    results: list[CapabilityInfo | None] = [None] * _N_CALLERS
    lock = threading.Lock()

    def _slow_evaluator(_artifacts):
        with lock:
            calls["n"] += 1
        eval_started.set()
        assert release.wait(timeout=5), "evaluator was never released"
        return _verdict(CapabilityState.AVAILABLE, "authoritative")

    def caller(i: int) -> None:
        start.wait()
        results[i] = caps.get_capability(_FEATURE)

    monkeypatch.setattr(caps, "_evaluators", evaluators_for_target)

    threads = [threading.Thread(target=caller, args=(i,)) for i in range(_N_CALLERS)]
    for t in threads:
        t.start()
    assert eval_started.wait(timeout=5), "no caller reached the evaluator"
    time.sleep(0.05)
    release.set()
    for t in threads:
        t.join(timeout=5)
        assert not t.is_alive(), "caller never returned"

    assert calls["n"] == 1, (
        f"cold misses were not coalesced: {calls['n']} authoritative "
        f"evaluations for {_N_CALLERS} concurrent callers"
    )
    assert all(r is not None for r in results)
    first = results[0]
    assert first is not None
    assert all(r.state == first.state and r.details == first.details for r in results)


def test_full_invalidation_rearms_authoritative_recompute(monkeypatch) -> None:
    """clear_capability_cache() must drop in-flight state and force a fresh read."""
    real = caps._evaluators
    calls = {"n": 0}

    def evaluator(_artifacts):
        calls["n"] += 1
        state = (
            CapabilityState.AVAILABLE
            if calls["n"] == 1
            else CapabilityState.DISABLED_BY_LICENSE
        )
        return _verdict(state, f"call-{calls['n']}")

    def evaluators():
        base = real()
        base[_FEATURE] = evaluator
        return base

    monkeypatch.setattr(caps, "_evaluators", evaluators)

    first = caps.get_capability(_FEATURE)
    assert first.state is CapabilityState.AVAILABLE
    assert calls["n"] == 1

    caps.clear_capability_cache()
    second = caps.get_capability(_FEATURE)
    assert second.state is CapabilityState.DISABLED_BY_LICENSE
    assert calls["n"] == 2, "invalidation did not force a fresh authoritative read"


def test_coalescing_is_per_feature(monkeypatch) -> None:
    """Two different feature keys must not share an in-flight verdict."""
    other = "artifacts"
    real = caps._evaluators
    started = threading.Barrier(2)
    release = threading.Event()
    calls = {"target": 0, "other": 0}
    lock = threading.Lock()

    def evaluator_for(feature: str, key: str):
        def evaluate(_artifacts):
            with lock:
                calls[key] += 1
            started.wait(timeout=5)
            assert release.wait(timeout=5)
            return CapabilityInfo(
                feature=feature,
                state=CapabilityState.AVAILABLE,
                message=f"verdict for {feature}",
                details={},
            )

        return evaluate

    def evaluators():
        base = real()
        base[_FEATURE] = evaluator_for(_FEATURE, "target")
        base[other] = evaluator_for(other, "other")
        return base

    monkeypatch.setattr(caps, "_evaluators", evaluators)

    results: dict[str, CapabilityInfo | None] = {}

    def caller(feature: str) -> None:
        results[feature] = caps.get_capability(feature)

    threads = [threading.Thread(target=caller, args=(f,)) for f in (_FEATURE, other)]
    for t in threads:
        t.start()
    # Both evaluators must start: a single shared flight would have blocked the
    # second feature behind the first (Barrier would time out).
    release.set()
    for t in threads:
        t.join(timeout=5)
        assert not t.is_alive()

    assert calls == {"target": 1, "other": 1}
    assert results[_FEATURE] is not None and results[other] is not None
    assert results[_FEATURE].feature == _FEATURE
    assert results[other].feature == other
