"""RESUME must not restore privileged intents from a stale/cached session.

A gateway session outlives its socket and is persisted for cross-worker
resume.  IDENTIFY creates sessions with a validated, non-privileged intent
mask, but the cached mask is untrusted at resume time: a stale record (from
before privileged intents required approval) or a corrupted value must not be
handed verbatim to a resuming connection, or RESUME becomes a privilege
re-escalation path that regains event visibility IDENTIFY would deny.
"""

from src.api.websocket.connection import Connection
from src.api.websocket.intents import PRIVILEGED_INTENTS
from src.api.websocket.session import SessionManager, _safe_resume_intents
from src.core.events.types import GatewayIntent


def _manager():
    return SessionManager(
        heartbeat_interval_ms=45000,
        session_timeout_ms=60000,
        max_connections_per_user=5,
    )


def _new_connection(sm):
    return Connection(
        websocket=None,
        connection_id=sm.generate_connection_id(),
        heartbeat_interval_ms=45000,
    )


def test_resume_strips_privileged_intents_from_cached_session():
    """A stale session holding privileged bits regains none of them on resume."""
    sm = _manager()
    old = _new_connection(sm)
    sm.add_connection(old)
    # Simulate a stale/persisted session that still carries a privileged mask.
    session = sm.create_session(old, user_id=42, intents=PRIVILEGED_INTENTS)

    new = _new_connection(sm)
    sm.add_connection(new)
    resumed = sm.resume_session(new, session.session_id, 0)

    assert resumed is not None
    assert new.intents == 0
    assert (new.intents & PRIVILEGED_INTENTS) == 0
    # The cached/persisted value is healed too, so a second resume cannot
    # re-escalate from the same stale record.
    assert session.intents == 0


def test_resume_strips_only_privileged_bits_from_valid_mask():
    """Non-privileged intents survive; MESSAGE_CONTENT is removed."""
    sm = _manager()
    old = _new_connection(sm)
    sm.add_connection(old)
    mask = int(GatewayIntent.GUILDS) | int(GatewayIntent.MESSAGE_CONTENT)
    session = sm.create_session(old, user_id=42, intents=mask)

    new = _new_connection(sm)
    sm.add_connection(new)
    assert sm.resume_session(new, session.session_id, 0) is not None

    assert new.intents == int(GatewayIntent.GUILDS)
    assert (new.intents & PRIVILEGED_INTENTS) == 0


def test_safe_resume_intents_fails_closed_on_invalid_masks():
    """Corrupt masks grant no intents rather than being treated as 'all'."""
    assert _safe_resume_intents(-1) == 0
    assert _safe_resume_intents(True) == 0
    assert _safe_resume_intents("all") == 0
    assert _safe_resume_intents(1 << 40) == 0
