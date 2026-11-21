"""Regression: inbound frame-limit config is clamped once per burst, not per frame.

The gateway frame guard resolves ``max_message_size`` / ``max_decompressed_size``
on every inbound frame. That resolution is a pure function of two config scalars,
so it is memoized in :mod:`src.api.websocket.compression`. These tests pin the
observable behaviour: a burst of identical frames clamps the values once, the
memo follows config changes, and correctness (type-sensitive fail-closed
clamping) is unchanged.
"""

import pytest

import src.api.websocket.compression as c
from src.api.websocket.frames import inbound_frame_limit


@pytest.fixture(autouse=True)
def _clean_limits_memo():
    # Isolate counters/cache between tests regardless of global test config.
    c.clear_limits_cache()
    yield
    c.clear_limits_cache()


def _fake_config(monkeypatch, websocket_value):
    """Serve a controlled ``websocket`` config value from ``config.get``."""

    def fake_get(key, default=None):
        if key == "websocket":
            return websocket_value
        return default

    monkeypatch.setattr(c.config, "get", fake_get)


def test_burst_clamps_once_not_per_frame(monkeypatch):
    """The clamp counter is the before/after measure.

    Before memoization, 50 inbound frames performed 2 * 50 = 100 defensive
    ``bounded_int`` clamps. After, exactly 2 clamps run (one per field) and the
    other 49 frames are memo hits.
    """
    _fake_config(
        monkeypatch,
        {"max_message_size": 65536, "max_decompressed_size": 262144},
    )
    calls = {"n": 0}
    real_clamp = c.clamp_configured_limit

    def counting_clamp(*args, **kwargs):
        calls["n"] += 1
        return real_clamp(*args, **kwargs)

    monkeypatch.setattr(c, "clamp_configured_limit", counting_clamp)

    for _ in range(50):
        assert c._get_limits() == (65536, 262144)

    assert calls["n"] == 2, "config scalars were re-clamped per frame"
    stats = c.get_limits_cache_stats()
    assert stats["misses"] == 1
    assert stats["hits"] == 49
    assert stats["size"] == 1


def test_memoized_values_match_fresh_clamp(monkeypatch):
    """Fail-closed clamping is identical with and without the memo."""
    # Oversized value clamps to the frame ceiling; an unparseable string and a
    # bool fall back to the built-in defaults.
    _fake_config(
        monkeypatch,
        {
            "max_message_size": 10**9,
            "max_decompressed_size": "not-a-number",
        },
    )
    assert c._get_limits() == (
        c.DEFAULT_MAX_MESSAGE_SIZE,
        c.DEFAULT_MAX_DECOMPRESSED_SIZE,
    )

    _fake_config(
        monkeypatch,
        {"max_message_size": True, "max_decompressed_size": None},
    )
    assert c._get_limits() == (
        c.DEFAULT_MAX_MESSAGE_SIZE,
        c.DEFAULT_MAX_DECOMPRESSED_SIZE,
    )


def test_config_change_invalidates_and_type_keys_do_not_collide(monkeypatch):
    """A changed config value yields a fresh clamp and never reuses a stale one."""
    holder = {"value": {"max_message_size": 1000, "max_decompressed_size": 4096}}
    monkeypatch.setattr(
        c.config,
        "get",
        lambda key, default=None: holder["value"] if key == "websocket" else default,
    )

    assert c._get_limits() == (1000, 4096)
    assert c._get_limits() == (1000, 4096)
    stats = c.get_limits_cache_stats()
    assert stats["misses"] == 1
    assert stats["hits"] == 1

    # int 1 and bool True must not share an entry: bounded_int rejects bool.
    holder["value"] = {"max_message_size": True, "max_decompressed_size": 4096}
    assert c._get_limits() == (c.DEFAULT_MAX_MESSAGE_SIZE, 4096)
    assert c.get_limits_cache_stats()["misses"] == 2


def test_memo_is_bounded(monkeypatch):
    """Config churn cannot grow the memo without limit."""
    counter = {"n": 0}

    def fake_get(key, default=None):
        if key != "websocket":
            return default
        counter["n"] += 1
        return {"max_message_size": counter["n"], "max_decompressed_size": 262144}

    monkeypatch.setattr(c.config, "get", fake_get)
    for _ in range(c._LIMITS_CACHE_MAX + 5):
        c._get_limits()
    assert c.get_limits_cache_stats()["size"] <= c._LIMITS_CACHE_MAX


def test_inbound_frame_limit_reuses_memoized_limits(monkeypatch):
    """The per-frame guard reads the memoized value and follows config changes."""
    holder = {"value": {"max_message_size": 1024, "max_decompressed_size": 4096}}
    monkeypatch.setattr(
        c.config,
        "get",
        lambda key, default=None: holder["value"] if key == "websocket" else default,
    )
    assert inbound_frame_limit() == 1024
    assert inbound_frame_limit() == 1024
    assert c.get_limits_cache_stats()["misses"] == 1

    holder["value"] = {"max_message_size": 2048, "max_decompressed_size": 4096}
    assert inbound_frame_limit() == 2048
    assert c.get_limits_cache_stats()["misses"] == 2
