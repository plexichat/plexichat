"""Regression: the per-user conversation purge must batch its spare checks.

``purge_user_conversations`` used to issue two probes *per conversation* the
erasing user had joined -- one ``COUNT`` against ``msg_participants`` (does
anybody else still participate?) and one ``SELECT`` against ``srv_channels``
(is this conversation still owned by a live server channel?). A user in a few
thousand DMs therefore paid a few thousand round trips during a single GDPR
purge, and neither probe could be bounded as one statement.

The fix resolves both predicates in bounded ``IN`` chunks before the user's own
participant rows are deleted. This pins the query-count bound and the safety
contract: a conversation with another live participant, or one still owned by a
server channel, is spared; an empty one is erased.
"""

from __future__ import annotations

import math
import uuid
from unittest.mock import patch

import pytest

from src.utils import encryption
from src.utils.encryption import generate_snowflake_id

pytestmark = pytest.mark.integration

CHUNK = 400
# Span two chunks so chunking -- not just batching -- is exercised.
N = CHUNK * 2 + 50


class _RecordingDB:
    """Delegate to the real DB while recording every emitted statement."""

    def __init__(self, real):
        self._real = real
        self.calls = []

    def fetch_one(self, sql, params=None):
        self.calls.append(("fetch_one", sql, tuple(params or ())))
        return self._real.fetch_one(sql, params)

    def fetch_all(self, sql, params=None):
        self.calls.append(("fetch_all", sql, tuple(params or ())))
        return self._real.fetch_all(sql, params)

    def execute(self, sql, params=None, **kwargs):
        self.calls.append(("execute", sql, tuple(params or ())))
        return self._real.execute(sql, params, **kwargs)


def _register(auth_manager, pii_gen):
    unique = uuid.uuid4().hex[:8]
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        return auth_manager.register(
            username=f"probe_{unique}",
            email=pii_gen.email(),
            password="TestPass123!",
        )


def _insert_conversation(db, *, conversation_id, user_id):
    now = 1_700_000_000_000
    db.execute(
        "INSERT INTO msg_conversations "
        "(id, conversation_type, created_at, updated_at) VALUES (?, 'dm', ?, ?)",
        (conversation_id, now, now),
    )
    db.execute(
        "INSERT INTO msg_participants "
        "(id, conversation_id, user_id, role, joined_at) "
        "VALUES (?, ?, ?, 'member', ?)",
        (generate_snowflake_id(), conversation_id, user_id, now),
    )


def test_conversation_spare_probes_are_batched_and_bounded(db, auth_manager, pii_gen):
    from src.core.erasure import purge_user_conversations

    uid = int(_register(auth_manager, pii_gen).id)
    conversation_ids = []
    for _ in range(N):
        cid = generate_snowflake_id()
        conversation_ids.append(cid)
        _insert_conversation(db, conversation_id=cid, user_id=uid)

    spy = _RecordingDB(db)
    removed = purge_user_conversations(spy, uid)

    # Every empty conversation the user was alone in must still be erased.
    assert set(removed) == set(conversation_ids)

    participant_probes = [
        c for c in spy.calls if "msg_participants" in c[1] and "user_id != ?" in c[1]
    ]
    channel_probes = [
        c for c in spy.calls if "srv_channels" in c[1] and "deleted = 0" in c[1]
    ]

    # One query per bounded chunk, never one per conversation.
    expected = math.ceil(N / CHUNK)
    assert len(participant_probes) <= expected, (
        f"expected <= {expected} batched participant probes, "
        f"got {len(participant_probes)} (N={N})"
    )
    assert len(channel_probes) <= expected, (
        f"expected <= {expected} batched live-channel probes, "
        f"got {len(channel_probes)} (N={N})"
    )
    for _kind, sql, params in participant_probes + channel_probes:
        assert sql.count("?") <= CHUNK + 1
        assert len(params) <= CHUNK + 1


def test_batched_probes_still_spare_shared_and_channel_conversations(
    db, auth_manager, pii_gen
):
    from src.core.erasure import purge_user_conversations

    uid = int(_register(auth_manager, pii_gen).id)
    other = int(_register(auth_manager, pii_gen).id)
    shared = generate_snowflake_id()
    channel_backed = generate_snowflake_id()
    empty = generate_snowflake_id()
    now = 1_700_000_000_000

    for cid in (shared, channel_backed, empty):
        _insert_conversation(db, conversation_id=cid, user_id=uid)

    # Another, still-live participant keeps `shared`.
    db.execute(
        "INSERT INTO msg_participants "
        "(id, conversation_id, user_id, role, joined_at) "
        "VALUES (?, ?, ?, 'member', ?)",
        (generate_snowflake_id(), shared, other, now),
    )
    # A live server channel keeps `channel_backed`.
    server_id = generate_snowflake_id()
    db.execute(
        "INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) "
        "VALUES (?, 'owner', ?, ?, ?)",
        (server_id, other, now, now),
    )
    db.execute(
        "INSERT INTO srv_channels "
        "(id, server_id, name, channel_type, position, conversation_id, "
        " created_at, updated_at) "
        "VALUES (?, ?, 'general', 'text', 0, ?, ?, ?)",
        (generate_snowflake_id(), server_id, channel_backed, now, now),
    )

    removed = purge_user_conversations(db, uid)

    assert set(removed) == {empty}
    for kept in (shared, channel_backed):
        assert db.fetch_one("SELECT id FROM msg_conversations WHERE id = ?", (kept,)), (
            f"a conversation that is still in use must survive: {kept}"
        )
