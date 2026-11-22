"""Replay-buffer size cache must be cheaper without weakening any guard.

Recording and resuming a session used to re-serialize the whole replay buffer
on every resume (and again after hydration), even though every event had
already been measured when it was written. The cache must remove that work
while leaving the per-event ceiling, the total-byte budget and the backstop
against direct mutation of ``replay_events`` exactly as strict as before, and
it must never share measurements between sessions.
"""

import src.api.websocket.session as smod
from src.api.websocket.boundaries import (
    MAX_REPLAY_EVENT_BYTES,
    MAX_SESSION_REPLAY_BYTES,
    payload_size,
)
from src.api.websocket.session import Session


def _event(seq, body):
    return {"s": seq, "t": "MESSAGE_CREATE", "d": {"body": body}}


def test_resume_uses_cache_and_returns_identical_events(monkeypatch):
    """An unmodified buffer needs zero re-serialization and yields the same data."""
    events = [_event(i, "x" * (32 * 1024)) for i in range(1, 21)]
    session = Session(session_id="s", user_id=1, replay_events=list(events))
    expected = [
        event for event in events if payload_size(event) <= MAX_REPLAY_EVENT_BYTES
    ]

    real_payload_size = smod.payload_size
    calls = []

    def counting(value):
        calls.append(value)
        return real_payload_size(value)

    monkeypatch.setattr(smod, "payload_size", counting)

    assert session.get_replay_events(0) == expected
    assert calls == [], "resume re-serialized the replay buffer"


def test_hydration_measures_each_event_once(monkeypatch):
    """Hydration seeds the cache from the sanitize pass; no second full scan."""
    events = [_event(i, "y" * (48 * 1024)) for i in range(1, 21)]
    real_payload_size = smod.payload_size
    calls = []

    def counting(value):
        calls.append(value)
        return real_payload_size(value)

    monkeypatch.setattr(smod, "payload_size", counting)
    session = Session(session_id="h", user_id=1, replay_events=list(events))

    assert len(session.replay_events) == 20
    assert len(calls) == 20, f"hydration measured {len(calls)} times, expected 20"
    assert session.replay_bytes == sum(
        real_payload_size(e) for e in session.get_replay_events(0)
    )
    # The cache is immediately usable on the first resume.
    calls.clear()
    assert len(session.get_replay_events(0)) == 20
    assert calls == []


def test_direct_mutation_backstop_still_drops_oversized_event():
    """Appending a huge event straight to the buffer must not bypass the cap."""
    session = Session(session_id="s", user_id=1)
    session.add_replay_event(_event(1, "ok"))
    assert session.get_replay_events(0) == [_event(1, "ok")]  # warm the cache

    session.replay_events.append(_event(2, "x" * (MAX_REPLAY_EVENT_BYTES + 1)))
    session.replay_events.append(_event(3, "still-ok"))
    assert [event["s"] for event in session.get_replay_events(0)] == [1, 3]


def test_in_place_content_growth_invalidates_cached_size():
    """Mutating a stored event in place must re-measure it, not trust the cache."""
    session = Session(session_id="s", user_id=1)
    session.add_replay_event(_event(1, "ok"))
    assert session.get_replay_events(0) == [_event(1, "ok")]  # warm the cache

    session.replay_events[0]["d"]["body"] = "x" * (MAX_REPLAY_EVENT_BYTES + 1)
    assert session.get_replay_events(0) == []


def test_total_byte_budget_with_cache():
    """The 8 MiB total ceiling is still enforced across many cached writes."""
    session = Session(session_id="s", user_id=1, max_replay_events=1000)
    for i in range(50):
        session.add_replay_event(_event(i + 1, "x" * (200 * 1024)))

    assert session.replay_bytes <= MAX_SESSION_REPLAY_BYTES
    assert 0 < len(session.replay_events) < 50
    assert session.replay_events[0]["s"] > 1  # oldest evicted first
    assert (
        sum(payload_size(e) for e in session.replay_events) <= MAX_SESSION_REPLAY_BYTES
    )
    assert session.get_replay_events(0) == session.replay_events


def test_unicode_and_nested_events_match_fresh_measurement():
    """The structural key preserves size equivalence for non-ASCII/escaped data."""
    events = [
        {"s": 1, "d": {"text": "é" * 30000}},
        {"s": 2, "d": {"text": 'quote:" and \\' * 200}},
        {"s": 3, "d": [1, 2, 3, {"k": "v"}]},
    ]
    session = Session(session_id="u", user_id=1, replay_events=list(events))
    assert session.get_replay_events(0) == events


def test_unknown_objects_are_always_re_measured(monkeypatch):
    """Values routed through ``default=str`` never rely on a stale cached size."""

    class Weird:
        def __str__(self):
            return "W" * (MAX_REPLAY_EVENT_BYTES + 1)

    session = Session(session_id="w", user_id=1)
    event = {"s": 1, "d": Weird()}
    session.replay_events = [event]
    session._rebuild_replay_size_cache()
    assert session._replay_size_cache[0][0] is smod._UNCACHEABLE
    assert session.get_replay_events(0) == []


def test_size_cache_is_per_session_no_cross_session_leak():
    """One session's buffer/cache mutation cannot affect another's resume."""
    a = Session(session_id="a", user_id=1, replay_events=[_event(1, "x" * 1024)])
    b = Session(session_id="b", user_id=2, replay_events=[_event(1, "y" * 4096)])

    assert a._replay_size_cache is not b._replay_size_cache
    a_size = a._replay_size_cache[0][1]
    b_size = b._replay_size_cache[0][1]
    assert a_size != b_size
    assert [event["s"] for event in a.get_replay_events(0)] == [1]
    assert [event["s"] for event in b.get_replay_events(0)] == [1]

    # Mutating B's buffer and cache must leave A untouched.
    b.replay_events.append(_event(2, "z" * (MAX_REPLAY_EVENT_BYTES + 1)))
    b._replay_size_cache.clear()
    assert [event["s"] for event in a.get_replay_events(0)] == [1]
    assert [event["s"] for event in b.get_replay_events(0)] == [1]
    assert a._replay_size_cache[0][1] == a_size
