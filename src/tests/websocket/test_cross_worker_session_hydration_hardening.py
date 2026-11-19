"""Cross-worker session hydration must not bypass replay/intent guards.

A session created on worker A is rehydrated on worker B from Valkey when a
client resumes there. The local recording path (``Session.add_replay_event``)
enforces a per-event byte ceiling, but hydration used to copy the persisted
``replay_events`` verbatim (only slicing to the last 100 by count). A stale,
corrupt or otherwise oversized payload could therefore reintroduce a huge
frame into the replay buffer and have it replayed to a resuming client,
defeating the guard that every locally recorded event passes through.

Hydration also used to trust the persisted intent mask blindly. An invalid
mask (negative, boolean, unknown bits) applied by ``set_identified`` fails
closed at every intent check, silently blackholing the resumed connection.
Reject such a mask at the boundary instead, like IDENTIFY does.
"""

import time

import src.api.websocket.session as session_mod
from src.api.websocket.boundaries import (
    MAX_REPLAY_EVENT_BYTES,
    MAX_SESSION_REPLAY_BYTES,
)


class _FakeValkeyClient:
    worker_id = "worker-b"

    def __init__(self, payload):
        self._payload = payload

    def get_json(self, key):
        return self._payload


def _payload(**overrides):
    data = {
        "session_id": "deadbeefdeadbeef",
        "user_id": 42,
        "sequence": 1,
        "intents": 1,
        "created_at_epoch": time.time(),
        "last_activity_epoch": time.time(),
        "replay_events": [],
    }
    data.update(overrides)
    return data


def _hydrate(payload):
    manager = session_mod.SessionManager()
    original_available = session_mod.valkey_available
    original_client = session_mod.get_valkey_client
    session_mod.valkey_available = lambda: True
    session_mod.get_valkey_client = lambda: _FakeValkeyClient(payload)
    try:
        return manager._load_shared_session(payload["session_id"])
    finally:
        session_mod.valkey_available = original_available
        session_mod.get_valkey_client = original_client


def test_hydration_drops_oversized_replay_event():
    """An oversized persisted event must not survive hydration."""
    huge = "A" * (MAX_REPLAY_EVENT_BYTES + 1)
    session = _hydrate(
        _payload(replay_events=[{"s": 1, "t": "OK", "d": "small"}, {"s": 2, "d": huge}])
    )
    assert session is not None
    assert [event.get("s") for event in session.replay_events] == [1]


def test_hydration_drops_non_dict_replay_event():
    """A malformed persisted entry must not be replayed to a client."""
    session = _hydrate(_payload(replay_events=[{"s": 1}, "not-an-event", None]))
    assert session is not None
    assert session.replay_events == [{"s": 1}]


def test_hydration_enforces_total_replay_byte_budget():
    """Many individually-valid events must still fit a bounded total budget."""
    # Each event is ~64 KiB; 100 of them would be ~6.4 MiB, well above the
    # per-session total ceiling.
    event = {"s": 1, "d": "x" * (64 * 1024)}
    session = _hydrate(_payload(replay_events=[dict(event) for _ in range(100)]))
    assert session is not None
    total = sum(session_mod.payload_size(e) for e in session.replay_events)
    assert total <= MAX_SESSION_REPLAY_BYTES


def test_hydration_rejects_invalid_intents():
    """A corrupt intent mask must fail closed (no intents), not be applied."""
    for bad in (-1, True, 1 << 30):
        session = _hydrate(_payload(intents=bad))
        assert session is not None
        assert session.intents == 0


def test_local_recording_enforces_total_byte_budget():
    """Locally recorded events are evicted once the total budget is exceeded."""
    from src.api.websocket.session import Session

    session = Session(session_id="s", user_id=1, max_replay_events=1000)
    # ~200 KiB each (under the per-event ceiling); 50 of them exceed the
    # 8 MiB total budget while staying individually valid.
    for i in range(50):
        session.add_replay_event({"s": i + 1, "d": "x" * (200 * 1024)})
    assert session.replay_bytes <= MAX_SESSION_REPLAY_BYTES
    assert 0 < len(session.replay_events) < 50
    # Only the newest events survive; the oldest were evicted first.
    assert session.replay_events[0]["s"] > 1


def test_get_replay_events_rechecks_size_backstop():
    """Direct mutation of the buffer cannot reintroduce an oversized frame."""
    from src.api.websocket.session import Session

    session = Session(session_id="s", user_id=1)
    session.replay_events.append({"s": 1, "d": "x" * (MAX_REPLAY_EVENT_BYTES + 1)})
    session.replay_events.append({"s": 2, "d": "ok"})
    assert [event["s"] for event in session.get_replay_events(0)] == [2]
