"""Regressions for two gateway boundary weaknesses.

1. A WebSocket transport without ``headers`` / ``query_params`` attributes
   used to make the handshake origin/token gates fail OPEN (``getattr``
   defaults of ``{}`` / ``None`` looked like "no browser origin, no token").
2. ``SessionManager._sessions`` had no hard cap and no production janitor,
   so a ticket holder could grow the local session map (and its replay
   buffers) without bound by churning IDENTIFY on short-lived sockets.
"""

from types import SimpleNamespace

import pytest

import utils.config as config
from src.api.websocket import handshake as hs
from src.api.websocket import session as session_mod
from src.api.websocket.handshake import HandshakeRejected, validate_handshake
from src.api.websocket.session import Session, SessionManager


# --- Handshake: missing metadata must fail closed -------------------------


class BareWebSocket:
    """A transport exposing no handshake metadata attributes at all."""

    client = SimpleNamespace(host="203.0.113.9")


@pytest.fixture(autouse=True)
def _clean_handshake_state(monkeypatch):
    hs.reset_handshake_state()

    def _config(key, default=None):
        if key == "websocket":
            return {}
        if key == "api":
            return {"cors_origins": ["https://app.plexichat.com"]}
        return default

    monkeypatch.setattr(config, "get", _config)
    yield
    hs.reset_handshake_state()


def test_missing_headers_attribute_fails_closed():
    """No readable headers object must not silently skip the Origin gate."""
    with pytest.raises(HandshakeRejected) as exc:
        validate_handshake(BareWebSocket())
    assert "could not be inspected" in exc.value.reason


def test_missing_query_attribute_fails_closed():
    """A Socket with headers but no query_params must not skip the token gate."""
    ws = SimpleNamespace(
        headers={"Origin": "https://app.plexichat.com"},
        client=SimpleNamespace(host="203.0.113.9"),
    )
    with pytest.raises(HandshakeRejected) as exc:
        validate_handshake(ws)
    assert "query could not be inspected" in exc.value.reason


# --- Session map: hard cap ------------------------------------------------


@pytest.fixture
def _no_valkey(monkeypatch):
    monkeypatch.setattr(session_mod, "valkey_available", lambda: False)


def _make_manager(cap):
    return SessionManager(
        heartbeat_interval_ms=45000,
        session_timeout_ms=60000,
        max_connections_per_user=5,
        max_local_sessions=cap,
    )


def test_session_map_is_hard_bounded(_no_valkey):
    manager = _make_manager(3)
    for i in range(25):
        conn = ConnectionStub(f"conn-{i}")
        manager.add_connection(conn)
        manager.create_session(conn, user_id=100 + i, intents=0)

    assert len(manager._sessions) <= 3
    # The most recently created sessions are the ones still retained.
    assert len(manager._sessions) == 3


def test_eviction_prefers_stale_sessions_without_live_connection(_no_valkey):
    manager = _make_manager(3)
    live_a = ConnectionStub("live-a")
    live_b = ConnectionStub("live-b")
    manager.add_connection(live_a)
    manager.add_connection(live_b)
    keep_a = manager.create_session(live_a, user_id=1, intents=0)
    keep_b = manager.create_session(live_b, user_id=2, intents=0)

    # One stale, already-disconnected session plus a fresh one that pushes the
    # map over the cap. The stale, connection-less session must be evicted
    # first, preserving both live sessions.
    stale = Session(session_id="stale", user_id=99, connection_id="gone")
    stale.last_activity -= 10_000
    stale.last_activity_epoch -= 10_000
    manager._sessions["stale"] = stale

    cold = ConnectionStub("cold")
    manager.add_connection(cold)
    manager.create_session(cold, user_id=3, intents=0)

    # The one stale, connection-less session is shed first; both live sessions
    # and the new one survive without evicting a resumable peer.
    assert "stale" not in manager._sessions
    assert keep_a.session_id in manager._sessions
    assert keep_b.session_id in manager._sessions
    assert len(manager._sessions) == 3


def test_hydration_inserts_are_also_bounded(_no_valkey, monkeypatch):
    manager = _make_manager(2)
    counter = {"n": 0}

    def _fake_load(session_id):
        counter["n"] += 1
        return Session(session_id=f"remote-{counter['n']}", user_id=7)

    monkeypatch.setattr(manager, "_load_shared_session", _fake_load)
    for _ in range(10):
        manager.get_session(f"probe-{counter['n'] + 1}")
    assert len(manager._sessions) <= 2


class ConnectionStub:
    """Minimal connection for session-manager bookkeeping."""

    def __init__(self, connection_id):
        self.connection_id = connection_id
        self.user_id = None
        self.session_id = None
        self.state = None

    def set_identified(self, user_id, session_id, intents):
        self.user_id = user_id
        self.session_id = session_id
