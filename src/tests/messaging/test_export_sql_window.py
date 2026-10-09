"""Regression: transcript-export date bounds must filter before the limit.

``MessageRepository.get_by_conversation`` gained optional ``from_ts``/``to_ts``
(inclusive epoch-millisecond bounds on ``created_at``, applied in SQL) and an
``ascending`` flag, and ``TranscriptExportService._get_messages_for_export`` now
parses the date bounds *before* fetching and passes them through. Previously the
service fetched the newest ``max_messages`` rows (``ORDER BY id DESC``) and then
date-filtered in Python, so an export window older than those rows came back
empty, and every transcript was newest-first.
"""

import pytest

from src.core.messaging.repositories.message import MessageRepository
from src.core.messaging.services.export import TranscriptExportService

pytestmark = pytest.mark.messaging

# 2024-01-01T00:00:00Z and 2024-06-01T00:00:00Z.
_OLD_MS = 1_704_067_200_000
_NEW_MS = 1_717_200_000_000

# High, otherwise-unused ids so a session-scoped database cannot collide with
# rows another test already inserted.
_USER_ID = 9_900_001
_CONV_A = 9_900_011
_CONV_B = 9_900_012
_OLD_IDS = [9_900_101, 9_900_102, 9_900_103, 9_900_104, 9_900_105]
_NEW_IDS = [9_900_106, 9_900_107, 9_900_108, 9_900_109, 9_900_110]

_INSERT = (
    "INSERT INTO msg_messages "
    "(id, conversation_id, author_id, content, created_at, updated_at, message_type) "
    "VALUES (?, ?, ?, ?, ?, ?, 'text')"
)


class _StubParticipants:
    def exists(self, conversation_id, user_id):
        return True


def _prereqs(db, conversation_id):
    """Create the FK parents a message row needs (user, conversation, member)."""
    db.execute(
        "INSERT INTO auth_users (id, username, account_type, password_hash, "
        "permissions, created_at, updated_at) "
        "VALUES (?, 'sqlwindow', 'user', 'hash', '{}', 1000, 1000)",
        (_USER_ID,),
    )
    db.execute(
        "INSERT INTO msg_conversations (id, conversation_type, created_at, "
        "updated_at) VALUES (?, 'dm', 1000, 1000)",
        (conversation_id,),
    )
    db.execute(
        "INSERT INTO msg_participants (id, conversation_id, user_id, role, "
        "joined_at) VALUES (?, ?, ?, 'member', 1000)",
        (conversation_id, conversation_id, _USER_ID),
    )


def _seed(db, conversation_id, base_ms, ids):
    for offset, msg_id in enumerate(ids):
        ts = base_ms + offset
        db.execute(_INSERT, (msg_id, conversation_id, _USER_ID, f"m{msg_id}", ts, ts))


def test_repository_date_window_applies_before_the_limit(db):
    """A date window older than the newest ``limit`` rows is still returned."""
    _prereqs(db, _CONV_A)
    _seed(db, _CONV_A, _OLD_MS, _OLD_IDS)
    _seed(db, _CONV_A, _NEW_MS, _NEW_IDS)
    repo = MessageRepository(db)

    windowed = repo.get_by_conversation(
        _CONV_A, limit=5, from_ts=_OLD_MS, to_ts=_OLD_MS + 10, ascending=True
    )
    assert [int(m["id"]) for m in windowed] == _OLD_IDS

    # Defaults preserve the original newest-first, capped behaviour.
    newest = repo.get_by_conversation(_CONV_A, limit=5)
    assert [int(m["id"]) for m in newest] == list(reversed(_NEW_IDS))


def test_export_of_an_older_window_is_chronological(db):
    """The service passes the parsed bounds and ascending order to the repo."""
    _prereqs(db, _CONV_B)
    _seed(db, _CONV_B, _OLD_MS, _OLD_IDS)
    _seed(db, _CONV_B, _NEW_MS, _NEW_IDS)
    service = TranscriptExportService(None, MessageRepository(db), _StubParticipants())

    messages = service._get_messages_for_export(
        requester_id=_USER_ID,
        conversation_id=_CONV_B,
        from_date="2024-01-01",
        to_date="2024-01-31",
        max_messages=5,
    )
    assert [int(m["id"]) for m in messages] == _OLD_IDS
