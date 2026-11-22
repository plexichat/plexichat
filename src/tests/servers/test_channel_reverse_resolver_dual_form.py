"""Runtime regression for the central channel reverse resolver.

``ServerManager.get_channel_by_conversation`` is the membership-agnostic
resolver every channel-alias authorization gate leans on. It used to match
only ``srv_channels.conversation_id``, so passing a channel's own ``id`` (an
alias ``is_participant`` honours and that ``messages_crud`` can persist into a
message's conversation id) made the resolver return ``None`` and the gate
silently skip. This module executes the REAL resolver against a REAL SQLite
table -- no monkeypatched return values -- so both spellings and the
collision-preference guarantee are exercised, not merely asserted in a guard.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.core.servers.manager.base import ServerManager

pytestmark = [pytest.mark.security, pytest.mark.unit]


class _RowDB:
    """Minimal ``fetch_one`` adapter over a real sqlite3 connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def fetch_one(self, sql: str, params=()):
        return self.conn.execute(sql, params).fetchone()


class _ManagerStub:
    """Only the attribute the resolver method touches: ``self._db``."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._db = _RowDB(conn)


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE srv_channels ("
        "id INTEGER PRIMARY KEY, server_id INTEGER NOT NULL, "
        "conversation_id INTEGER, deleted INTEGER DEFAULT 0)"
    )
    yield conn
    conn.close()


def _resolve(db, value):
    return ServerManager.get_channel_by_conversation(_ManagerStub(db), value)


def _add(db, channel_id, conversation_id, server_id, deleted=0):
    db.execute(
        "INSERT INTO srv_channels (id, server_id, conversation_id, deleted) "
        "VALUES (?, ?, ?, ?)",
        (channel_id, server_id, conversation_id, deleted),
    )


def test_resolves_genuine_conversation_id(db):
    """Baseline: the authoritative linked conversation id still resolves."""
    _add(db, 10, 20, 1)
    assert _resolve(db, 20) == {"id": 10, "server_id": 1}


def test_resolves_channel_id_alias(db):
    """A channel's own id is an alias is_participant honours; it must resolve.

    Before the central fix this returned None, which is exactly what let a
    caller skip a miss-as-allow authorization gate by passing the channel id.
    """
    _add(db, 10, 20, 1)
    assert _resolve(db, 10) == {"id": 10, "server_id": 1}


def test_collision_prefers_conversation_id_match(db):
    """A genuine conversation id wins over an unrelated channel's numeric id.

    Channel A links conversation 20; channel B has id 20 but a different
    conversation. Passing 20 must still return A (the old single-form result),
    never B -- otherwise a gate would check the wrong server and could allow a
    caller who lacks permission on the channel they actually addressed.
    """
    _add(db, 10, 20, 1)  # A: conversation_id == 20
    _add(db, 20, 30, 2)  # B: id == 20, different conversation/server
    assert _resolve(db, 20) == {"id": 10, "server_id": 1}


def test_alias_still_resolves_when_no_conversation_match_exists(db):
    """With no linked row for the value, the id branch decides the alias."""
    _add(db, 10, 20, 1)
    _add(db, 20, 30, 2)
    assert _resolve(db, 30) == {"id": 20, "server_id": 2}


def test_deleted_channels_are_not_resolved_in_either_form(db):
    _add(db, 10, 20, 1, deleted=1)
    assert _resolve(db, 10) is None
    assert _resolve(db, 20) is None


def test_unmapped_id_returns_none(db):
    """A genuine DM-style id with no backing channel stays a miss."""
    _add(db, 10, 20, 1)
    assert _resolve(db, 999) is None
