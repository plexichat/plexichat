"""RESUME must revalidate a session's persisted creation time against an
absolute lifetime.

``created_at_epoch`` is persisted to Valkey and restored verbatim on a
cross-worker resume, but the only resume-validity check used to be the
``last_activity_epoch`` inactivity timeout. Activity is refreshed on every
resume and every dispatched event (and the Valkey TTL is reset on every
persist), so a session minted long ago -- potentially before the user was
demoted, banned, or removed from a guild/artifact/SUM -- could be revived
indefinitely, replaying an authorization context that no longer exists.

The fix fail-closes on a missing/corrupt/absurdly future creation time and
rejects sessions older than an absolute ceiling, on both the local and the
cross-worker hydration paths, without weakening the existing timeout check.
"""

import time

import src.api.websocket.session as session_mod
from src.api.websocket.connection import Connection
from src.api.websocket.session import (
    MAX_SESSION_LIFETIME_MS,
    SessionManager,
)


USER_ID = 4242


def _manager(**kwargs):
    return SessionManager(
        heartbeat_interval_ms=45000,
        session_timeout_ms=60000,
        max_connections_per_user=5,
        **kwargs,
    )


def _connection(sm):
    return Connection(
        websocket=None,
        connection_id=sm.generate_connection_id(),
        heartbeat_interval_ms=45000,
    )


class _FakeValkeyClient:
    worker_id = "worker-b"

    def __init__(self, payload):
        self._payload = payload

    def get_json(self, key):
        return self._payload


def _hydrate(payload, *, manager=None):
    manager = manager or _manager()
    original_available = session_mod.valkey_available
    original_client = session_mod.get_valkey_client
    session_mod.valkey_available = lambda: True
    session_mod.get_valkey_client = lambda: _FakeValkeyClient(payload)
    try:
        return manager._load_shared_session(payload["session_id"])
    finally:
        session_mod.valkey_available = original_available
        session_mod.get_valkey_client = original_client


def _payload(**overrides):
    data = {
        "session_id": "deadbeefdeadbeef",
        "user_id": USER_ID,
        "sequence": 1,
        "intents": 1,
        "created_at_epoch": time.time(),
        "last_activity_epoch": time.time(),
        "replay_events": [],
    }
    data.update(overrides)
    return data


def test_can_resume_rejects_session_past_absolute_lifetime():
    """Fresh activity must not resurrect a session older than the ceiling."""
    sm = _manager()
    conn = _connection(sm)
    sm.add_connection(conn)
    session = sm.create_session(conn, USER_ID, 513)

    # Simulate a long-lived session: activity is fresh (it would pass the
    # inactivity timeout), but creation predates the lifetime ceiling.
    session.last_activity_epoch = time.time()
    session.created_at_epoch = time.time() - MAX_SESSION_LIFETIME_MS / 1000 - 1

    assert sm.can_resume_session(session.session_id, USER_ID) is False
    # The stale record is evicted, so a sibling worker cannot rehydrate it.
    assert session.session_id not in sm._sessions


def test_resume_session_directly_refuses_expired_creation_time():
    """Direct resume_session callers (skipping can_resume) are also protected."""
    sm = _manager()
    old_conn = _connection(sm)
    sm.add_connection(old_conn)
    session = sm.create_session(old_conn, USER_ID, 513)
    session.created_at_epoch = time.time() - MAX_SESSION_LIFETIME_MS / 1000 - 1
    session.last_activity_epoch = time.time()

    new_conn = _connection(sm)
    sm.add_connection(new_conn)
    assert sm.resume_session(new_conn, session.session_id, 0) is None
    assert new_conn.is_authenticated is False


def test_hydration_rejects_stale_persisted_creation_time():
    """A cross-worker session past its lifetime is never hydrated."""
    stale = _payload(
        created_at_epoch=time.time() - MAX_SESSION_LIFETIME_MS / 1000 - 1,
        # Activity was refreshed right before the worker died.
        last_activity_epoch=time.time(),
    )
    assert _hydrate(stale) is None


def test_hydration_fails_closed_on_missing_or_invalid_creation_time():
    """Missing, corrupt, boolean and far-future creation times all fail closed."""
    missing = _payload()
    del missing["created_at_epoch"]
    assert _hydrate(missing) is None
    assert _hydrate(_payload(created_at_epoch=None)) is None
    assert _hydrate(_payload(created_at_epoch=True)) is None
    assert _hydrate(_payload(created_at_epoch=float("nan"))) is None
    assert _hydrate(_payload(created_at_epoch=time.time() + 3600)) is None


def test_fresh_session_still_resumes_with_lifetime_check_enabled():
    """Control: the new check does not reject a healthy session."""
    sm = _manager()
    old_conn = _connection(sm)
    sm.add_connection(old_conn)
    session = sm.create_session(old_conn, USER_ID, 513)

    assert sm.can_resume_session(session.session_id, USER_ID) is True
    new_conn = _connection(sm)
    sm.add_connection(new_conn)
    assert sm.resume_session(new_conn, session.session_id, 0) is session
    assert new_conn.is_authenticated is True


def test_fresh_cross_worker_session_still_hydrates():
    """Control: a valid persisted creation time hydrates normally."""
    assert _hydrate(_payload()) is not None
