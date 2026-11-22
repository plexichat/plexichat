"""Bounded benchmark/regression for snowflake id-field classification.

``stringify_snowflake_ids`` is the single source of truth for gateway id
serialization and is applied on every outbound payload. For every dict key it
calls ``is_id_field`` -- a pure function of a short string, with a tiny,
repeating vocabulary (``id`` / ``*_id`` / ``*_ids`` / ``*Id`` / ``*Ids``). The
same handful of keys is classified over and over across a fan-out, so the
four ``str.endswith`` probes per key dominate serialization cost.

This test bounds that repeated computation: it compares the shipped
``is_id_field`` against an uncached reference that runs the identical logic.
Before the memoization fix the two are the same code and the timing ratio is
~1.0 (test fails); after the fix repeated keys are served from a bounded LRU
and the memoized path is materially faster (test passes). A cache-counter
assertion plus an output-equality assertion keep the proof deterministic.
"""

import gc
import time

from src.core.events.snowflakes import is_id_field, stringify_snowflake_ids

# Representative recurring keys from a real gateway message payload.
_KEYS = [
    "id", "channel_id", "server_id", "guild_id", "author", "content",
    "timestamp", "edited_timestamp", "tts", "mention_everyone", "mentions",
    "mention_roles", "attachments", "embeds", "reactions", "pinned", "type",
    "avatar", "username", "count", "me",
]
# Repeated many times: keys recur across every payload in a fan-out.
_PROBE_KEYS = _KEYS * 40          # 800 classifications per pass
_REPS = 100                      # 80,000 classifications per timed pass


def _uncached_is_id_field(key: str) -> bool:
    """Reference implementation: the pre-fix, unmemoized classification."""
    return (
        key == "id"
        or key.endswith("_id")
        or key.endswith("_ids")
        or key.endswith("Id")
        or key.endswith("Ids")
    )


def test_id_field_classification_is_memoized():
    # cache_info only exists once the pure classifier is memoized. Before the
    # fix this attribute is missing and the test fails outright.
    is_id_field.cache_clear()
    for key in _PROBE_KEYS:
        assert is_id_field(key) == _uncached_is_id_field(key)
    info = is_id_field.cache_info()
    assert info.misses == len(set(_PROBE_KEYS))  # one miss per distinct key
    assert info.hits == len(_PROBE_KEYS) - info.misses


def test_memoized_classification_is_faster_than_recomputing():
    # Warm both paths so import/first-call costs do not skew the comparison.
    for key in _PROBE_KEYS[: len(_KEYS)]:
        _uncached_is_id_field(key)
        is_id_field(key)

    gc_was_enabled = gc.isenabled()
    gc.disable()
    try:
        t0 = time.perf_counter()
        for _ in range(_REPS):
            for key in _PROBE_KEYS:
                _uncached_is_id_field(key)
        t_ref = time.perf_counter() - t0

        t0 = time.perf_counter()
        for _ in range(_REPS):
            for key in _PROBE_KEYS:
                is_id_field(key)
        t_cached = time.perf_counter() - t0
    finally:
        if gc_was_enabled:
            gc.enable()

    # The uncached path pays 4 endswith probes per call; the memoized path is
    # a dict lookup. Require a clear (>25%) win, robust to scheduler noise.
    assert t_cached < t_ref * 0.75, (
        f"memoized={t_cached:.4f}s uncached={t_ref:.4f}s "
        f"ratio={t_cached / t_ref:.2f}"
    )


def test_stringify_output_is_unchanged_by_memoization():
    payload = {
        "id": 123, "channel_id": 456, "server_id": 789,
        "author": {"id": 42, "username": "u", "valid": True, "grid": 1},
        "content": "hi", "timestamp": 1700000000000,
        "mention_roles": [7, 8], "mentions": [{"id": 9, "name": "m"}],
        "userId": 10, "channelIds": [11, 12], "me": False,
    }
    out = stringify_snowflake_ids(payload)
    assert out == {
        "id": "123", "channel_id": "456", "server_id": "789",
        "author": {"id": "42", "username": "u", "valid": True, "grid": 1},
        "content": "hi", "timestamp": 1700000000000,
        "mention_roles": [7, 8],  # not an id field (_roles) - stays numeric
        "mentions": [{"id": "9", "name": "m"}],
        "userId": "10", "channelIds": ["11", "12"], "me": False,
    }
