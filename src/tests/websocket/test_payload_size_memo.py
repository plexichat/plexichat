"""Bounded replay-payload size memo must be fast without weakening the guard.

``payload_size`` is the byte ceiling for every gateway replay frame. Session
hydration/resume measures each replay event with it, and a broadcast frame is
measured once per session/recipient. The memo must remove the repeated
serialization while leaving the exact byte computation -- and therefore the
per-event and total replay budgets -- unchanged.

Fail-closed rules covered here:
  * a miss runs the original ``json.dumps`` computation in full;
  * a hit is served only for the same object whose fingerprint still proves
    byte-equivalent JSON (in-place mutation forces a fresh measurement);
  * values the fingerprint cannot prove (custom objects via ``default=str``,
    tuples, non-string keys, oversized keys) are never memoized;
  * cheap payloads are re-measured instead of paying validation cost;
  * the memo is bounded and never returns a stale size.
"""

import json

import src.api.websocket.session as session_mod
from src.api.websocket.boundaries import (
    MAX_OP_PAYLOAD_BYTES,
    MAX_REPLAY_EVENT_BYTES,
    clear_payload_size_cache,
    get_payload_size_cache_stats,
    payload_size,
)
from src.api.websocket.session import Session


def _fresh(value):
    """Original, uncached serialization size -- the oracle for every test."""
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))
    except (TypeError, ValueError, RecursionError):
        return MAX_OP_PAYLOAD_BYTES + 1


def _event(seq, body="ok"):
    return {
        "op": 0,
        "t": "MESSAGE_CREATE",
        "s": seq,
        "d": {"id": str(100000000000000000 + seq), "content": body},
    }


def _large_event(seq=1, fill="x"):
    return _event(seq, fill * (8 * 1024))


def setup_function(function):
    clear_payload_size_cache()


def test_large_repeated_payload_is_measured_once():
    event = _large_event()
    expected = _fresh(event)

    results = [payload_size(event) for _ in range(50)]

    assert results == [expected] * 50
    stats = get_payload_size_cache_stats()
    assert stats["hits"] == 49
    assert stats["misses"] == 1
    assert stats["size"] == 1


def test_hits_never_decrement_the_miss_counter():
    """``record_hit`` must not rewind ``misses`` that ``lookup`` never counted.

    ``lookup`` increments ``misses`` only when no live entry exists. A hit
    therefore has no matching miss to undo; subtracting one drives ``misses``
    negative and corrupts the cache's hit/miss observability stats.
    """
    event = _large_event()
    assert payload_size(event) == _fresh(event)  # the one and only miss

    for _ in range(32):
        assert payload_size(event) == _fresh(event)  # served from the memo

    stats = get_payload_size_cache_stats()
    assert stats["hits"] == 32
    assert stats["misses"] == 1
    assert stats["misses"] >= 0


def test_cheap_payloads_are_not_memoized():
    """Validating a small frame costs more than just measuring it again."""
    event = _event(1, "small body")
    expected = _fresh(event)
    assert expected < 2048

    for _ in range(20):
        assert payload_size(event) == expected

    stats = get_payload_size_cache_stats()
    assert stats["hits"] == 0
    assert stats["misses"] == 20
    assert stats["size"] == 0


def test_equal_but_distinct_objects_get_the_same_size():
    a = _large_event(1, "a")
    b = _large_event(1, "a")
    assert a is not b
    assert payload_size(a) == _fresh(a)
    assert payload_size(b) == _fresh(b) == payload_size(a)


def test_in_place_content_change_is_never_served_from_stale_cache():
    event = _large_event(1, "a")
    assert payload_size(event) == _fresh(event)

    # Same object, different bytes: the fingerprint must change and force a
    # fresh measurement (this is what keeps the byte guard sound).
    event["d"]["content"] = "b" * (8 * 1024)
    assert payload_size(event) == _fresh(event)

    # Growth beyond the per-event ceiling must still be detected.
    event["d"]["content"] = "c" * (MAX_REPLAY_EVENT_BYTES + 1)
    assert payload_size(event) == _fresh(event)
    assert payload_size(event) > MAX_REPLAY_EVENT_BYTES


def test_oversized_frame_still_crosses_the_ceiling():
    huge = {"s": 1, "d": "A" * (MAX_REPLAY_EVENT_BYTES + 1)}
    assert payload_size(huge) == _fresh(huge)
    assert payload_size(huge) > MAX_REPLAY_EVENT_BYTES


def test_unicode_escaping_nesting_and_floats_match_fresh_measurement():
    values = [
        {"s": 1, "d": {"text": "é" * 8000}},
        {"s": 2, "d": {"quoted": 'quote:" and \\' * 800}},
        {"s": 3, "d": [1, 2, 3, {"k": "v" * 4096}]},
        {"s": 4, "d": float("inf")},
        {"s": 5, "d": float("nan")},
    ]
    for value in values:
        assert payload_size(value) == _fresh(value)
        assert payload_size(value) == _fresh(value)


def test_uncacheable_values_are_always_re_measured():
    class Weird:
        def __str__(self):
            return "W" * (MAX_REPLAY_EVENT_BYTES + 1)

    values = [
        {"s": 1, "d": Weird()},  # default=str object
        {"s": 2, "d": (1, 2, 3)},  # tuple serializes as list but is not proven
        {1: "non-string key"},  # json coerces the key
    ]
    for value in values:
        assert payload_size(value) == _fresh(value)
        assert payload_size(value) == _fresh(value)
    stats = get_payload_size_cache_stats()
    assert stats["hits"] == 0
    assert stats["size"] == 0


def test_memo_is_bounded():
    stats = get_payload_size_cache_stats()
    for i in range(stats["max_entries"] * 3):
        event = _large_event(i, "x")
        assert payload_size(event) == _fresh(event)
    after = get_payload_size_cache_stats()
    assert after["size"] <= after["max_entries"]


def test_clear_resets_counters_and_entries():
    event = _large_event()
    payload_size(event)
    payload_size(event)
    assert get_payload_size_cache_stats()["hits"] == 1

    clear_payload_size_cache()
    stats = get_payload_size_cache_stats()
    assert stats == {
        "size": 0,
        "max_entries": stats["max_entries"],
        "hits": 0,
        "misses": 0,
    }


def test_session_hydration_reuses_the_measurement_across_sessions():
    """The broadcast replay-frame case: one serialize, many sessions."""
    events = [_large_event(i) for i in range(1, 11)]
    expected = [e for e in events if payload_size(e) <= MAX_REPLAY_EVENT_BYTES]

    clear_payload_size_cache()
    sessions = [
        Session(session_id=f"session-{n}", user_id=n, replay_events=list(events))
        for n in range(1, 9)
    ]

    for session in sessions:
        assert session.get_replay_events(0) == expected

    stats = get_payload_size_cache_stats()
    assert stats["misses"] == len(events), "each distinct event measured once"
    assert stats["hits"] >= len(events) * (len(sessions) - 1)
    assert session_mod.payload_size is payload_size
