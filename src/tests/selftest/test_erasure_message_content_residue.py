"""Regression: account erasure must scrub every copy of a user's message content.

``AccountReaper.purge_user`` rewrites only ``msg_messages.content`` when the
default ``anonymize_content`` policy is active. A message the user authored in
a conversation that survives for other participants keeps:

* ``content_encrypted`` -- the legacy ciphertext mirror of the plaintext;
* ``content_index`` -- a keyed blind index of the whole plaintext;
* ``msg_message_tokens`` -- one keyed blind index per plaintext word;
* ``msg_edit_history`` -- previous plaintext/ciphertext versions of the text.

SQLite purge paths run with foreign keys off, so even messages in conversations
that ARE deleted leave orphaned token children behind unless they are removed
explicitly. ``erase_user_residue`` now scrubs all of these and this test pins
that behaviour, including that a third party's rows are untouched.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from src.core.erasure import (
    ANONYMIZED_CONTENT,
    REDACTED_HISTORY_CONTENT,
    delete_conversation,
    erase_user_residue,
    purge_user_message_residue,
)
from src.utils import encryption

pytestmark = pytest.mark.integration

CONV = 9_100_000_100
ERASED_MSG = 9_100_000_200
SURVIVOR_MSG = 9_100_000_201
NOW = 1_700_000_000_000


def _register(auth_manager, pii_gen, label):
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        return auth_manager.register(
            username=f"{label}_{uuid.uuid4().hex[:8]}",
            email=pii_gen.email(),
            password="TestPass123!",
        )


def _seed_conversation_with_two_participants(db, erased, survivor) -> None:
    db.execute(
        """INSERT INTO msg_conversations
           (id, conversation_type, max_participants, created_at, updated_at)
           VALUES (?, 'dm', 2, ?, ?)""",
        (CONV, NOW, NOW),
    )
    for user_id in (erased, survivor):
        db.execute(
            """INSERT INTO msg_participants
               (id, conversation_id, user_id, role, joined_at)
               VALUES (?, ?, ?, 'member', ?)""",
            (CONV + user_id, CONV, user_id, NOW),
        )


def test_erase_scrubs_all_message_content_residue(
    db, auth_manager, pii_gen, monkeypatch
):
    monkeypatch.setattr("src.core.erasure._media_manager", lambda: None)
    erased = _register(auth_manager, pii_gen, "ermsg_a").id
    survivor = _register(auth_manager, pii_gen, "ermsg_b").id
    _seed_conversation_with_two_participants(db, erased, survivor)

    db.execute(
        """INSERT INTO msg_messages
           (id, conversation_id, author_id, content, content_encrypted,
            content_index, created_at, updated_at, metadata)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            ERASED_MSG,
            CONV,
            erased,
            "TOP SECRET BODY",
            "LEGACY-CIPHERTEXT-MIRROR",
            "blind-index-of-whole-body",
            NOW,
            NOW,
            '{"embeds": [{"description": "private embed"}]}',
        ),
    )
    db.execute(
        """INSERT INTO msg_messages
           (id, conversation_id, author_id, content, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (SURVIVOR_MSG, CONV, survivor, "other persons message", NOW, NOW),
    )
    db.execute(
        "INSERT INTO msg_message_tokens (message_id, token_index, position) "
        "VALUES (?, ?, ?)",
        (ERASED_MSG, "blind-index-TOP", 0),
    )
    db.execute(
        "INSERT INTO msg_edit_history "
        "(id, message_id, editor_id, old_content, old_content_encrypted, "
        "edit_timestamp, version_number) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (9_100_000_300, ERASED_MSG, erased, "OLD SECRET", "OLD-CIPHER", NOW, 1),
    )

    result = erase_user_residue(db, erased)

    row = db.fetch_one("SELECT * FROM msg_messages WHERE id = ?", (ERASED_MSG,))
    assert row["content"] == ANONYMIZED_CONTENT
    assert row["content_encrypted"] is None
    assert row["content_index"] is None
    assert row["metadata"] is None
    assert (
        db.fetch_one(
            "SELECT 1 FROM msg_message_tokens WHERE message_id = ?", (ERASED_MSG,)
        )
        is None
    )
    history = db.fetch_one(
        "SELECT * FROM msg_edit_history WHERE editor_id = ?", (erased,)
    )
    assert history["old_content"] == REDACTED_HISTORY_CONTENT
    assert history["old_content_encrypted"] is None
    assert result["message_residue"] > 0

    # The conversation stays alive for the survivor and their content is intact.
    assert (
        db.fetch_one(
            "SELECT content FROM msg_messages WHERE id = ?", (SURVIVOR_MSG,)
        )["content"]
        == "other persons message"
    )
    assert (
        db.fetch_one(
            "SELECT 1 FROM msg_participants WHERE conversation_id = ? AND user_id = ?",
            (CONV, survivor),
        )
        is not None
    )


def test_delete_conversation_removes_orphan_token_children(
    db, auth_manager, pii_gen, monkeypatch
):
    monkeypatch.setattr("src.core.erasure._media_manager", lambda: None)
    erased = _register(auth_manager, pii_gen, "ermsg_c").id
    survivor = _register(auth_manager, pii_gen, "ermsg_d").id
    _seed_conversation_with_two_participants(db, erased, survivor)
    db.execute(
        """INSERT INTO msg_messages
           (id, conversation_id, author_id, content, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (ERASED_MSG, CONV, erased, "secret", NOW, NOW),
    )
    db.execute(
        "INSERT INTO msg_message_tokens (message_id, token_index, position) "
        "VALUES (?, ?, ?)",
        (ERASED_MSG, "blind-index-secret", 0),
    )

    delete_conversation(db, CONV)

    assert (
        db.fetch_one(
            "SELECT 1 FROM msg_message_tokens WHERE message_id = ?", (ERASED_MSG,)
        )
        is None
    )
    assert (
        db.fetch_one("SELECT 1 FROM msg_messages WHERE id = ?", (ERASED_MSG,)) is None
    )


def test_purge_message_residue_tolerates_partial_schema(db):
    # A partial/older schema without the optional blind-index/history tables
    # must not abort the rest of an erasure.
    assert purge_user_message_residue(db, 12_345) >= 0
