"""Regression tests for amortising the render-generation TTL sweep.

``render_generation.bump`` runs on every message create/edit/delete/ack. Its
TTL expiry sweep is an O(n) pass over the whole registry, so running it on
every write made a hot path O(n). The sweep is now capped at once per
``min(TTL, _SWEEP_INTERVAL_CAP_SECONDS)`` while the entry/byte caps remain hard
and are enforced on every write. These tests pin both halves of that contract:
the sweep is amortised, and no safety property (TTL expiry, hard caps, stale
generation prevention) is weakened by the deferral.
"""

import pytest

from src.core.messaging import render_generation as rg


class _Clock:
    """A controllable ``time.monotonic`` for deterministic TTL timing."""

    def __init__(self, now=1000.0):
        self.now = now

    def monotonic(self):
        return self.now


@pytest.fixture(autouse=True)
def _clean_generations():
    rg.clear()
    yield
    rg.clear()


@pytest.mark.unit
def test_ttl_sweep_is_amortized_across_bumps(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(rg, "time", clock)
    monkeypatch.setattr(rg, "_TTL_SECONDS", 5000.0)  # long, production-like
    monkeypatch.setattr(rg, "_SWEEP_INTERVAL_CAP_SECONDS", 60.0)
    monkeypatch.setattr(rg, "_MAX_ENTRIES", 50_000)
    monkeypatch.setattr(rg, "_MAX_BYTES", 16 * 1024 * 1024)

    for i in range(400):
        rg.bump(i)
    # The first bump sweeps (last sweep at 0, now 1000 >= 60); the rest don't.
    assert rg.stats()["sweeps"] == 1
    assert rg.stats()["entries"] == 400

    # Staying inside the sweep interval never runs another sweep, no matter
    # how many writes arrive.
    clock.now += 30.0
    for i in range(400, 800):
        rg.bump(i)
    assert rg.stats()["sweeps"] == 1
    assert rg.stats()["entries"] == 800

    # Crossing the interval runs exactly one more sweep.
    clock.now += 31.0
    rg.bump(5000)
    assert rg.stats()["sweeps"] == 2


@pytest.mark.unit
def test_short_ttl_keeps_exact_sweep_semantics(monkeypatch):
    """A TTL shorter than the cap must not be stretched by amortisation."""
    clock = _Clock()
    monkeypatch.setattr(rg, "time", clock)
    monkeypatch.setattr(rg, "_TTL_SECONDS", 5.0)
    monkeypatch.setattr(rg, "_SWEEP_INTERVAL_CAP_SECONDS", 60.0)
    monkeypatch.setattr(rg, "_MAX_ENTRIES", 10_000)
    monkeypatch.setattr(rg, "_MAX_BYTES", 16 * 1024 * 1024)

    rg.bump("stale-conversation")
    assert "stale-conversation" in rg._generations

    clock.now += 10.0  # past TTL and past min(TTL, cap) == 5s
    rg.bump("fresh-conversation")
    assert "stale-conversation" not in rg._generations
    assert "fresh-conversation" in rg._generations


@pytest.mark.unit
def test_hard_caps_hold_even_when_sweep_is_deferred(monkeypatch):
    """Amortising the TTL sweep must not weaken the hard entry/byte caps."""
    clock = _Clock()
    monkeypatch.setattr(rg, "time", clock)
    monkeypatch.setattr(rg, "_TTL_SECONDS", 5000.0)
    monkeypatch.setattr(rg, "_SWEEP_INTERVAL_CAP_SECONDS", 1e9)  # never sweeps
    monkeypatch.setattr(rg, "_MAX_ENTRIES", 3)
    monkeypatch.setattr(rg, "_MAX_BYTES", 16 * 1024 * 1024)

    for i in range(50):
        rg.bump(i)

    # No sweep is due at all, yet the hard entry cap is still enforced on
    # every write -- amortising the TTL sweep cannot weaken boundedness.
    assert rg.stats()["sweeps"] == 0
    assert rg.stats()["entries"] <= 3
    assert len(rg._generations) <= 3


@pytest.mark.unit
def test_expired_entry_never_revives_a_stale_generation(monkeypatch):
    """Deferring the sweep must not let an expired key serve an old page."""
    clock = _Clock()
    monkeypatch.setattr(rg, "time", clock)
    monkeypatch.setattr(rg, "_TTL_SECONDS", 5000.0)
    monkeypatch.setattr(rg, "_SWEEP_INTERVAL_CAP_SECONDS", 60.0)
    monkeypatch.setattr(rg, "_MAX_ENTRIES", 10_000)
    monkeypatch.setattr(rg, "_MAX_BYTES", 16 * 1024 * 1024)

    stale = rg.bump("victim")
    rg.bump("other")  # raises the high-water mark above `stale`
    clock.now += 5001.0  # victim's entry is expired, sweep not yet due again

    # get() drops the expired entry for the requested key and rebinds it to the
    # current high-water mark, so the stale generation can never be returned.
    assert rg.get("victim") != stale
    assert rg.get("victim") == rg.stats()["high_water"]
