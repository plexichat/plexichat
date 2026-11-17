"""Regression: account erasure must scrub notification/presence/profile residue.

``notif_notifications`` has foreign keys to users/messages/conversations but no
cascade, and presence, profile, push-token and chat-tracking tables have no
foreign key to ``auth_users`` at all. ``erase_user_residue`` used to purge only
servers, conversations and media, leaving the account's notifications (with
their message previews), status, bio, device tokens and recent-chat history
behind under a user id that no longer exists.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from src.core.erasure import delete_conversation, erase_user_residue
from src.utils import encryption
from src.utils.encryption import generate_snowflake_id

pytestmark = pytest.mark.integration

NOW = 1_700_000_000_000


def _register(auth_manager, pii_gen, label):
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        return auth_manager.register(
            username=f"{label}_{uuid.uuid4().hex[:8]}",
            email=pii_gen.email(),
            password="TestPass123!",
        )


def _conversation(db, uid):
    cid = generate_snowflake_id()
    db.execute(
        "INSERT INTO msg_conversations "
        "(id, conversation_type, created_at, updated_at) VALUES (?, 'direct', ?, ?)",
        (cid, NOW, NOW),
    )
    db.execute(
        "INSERT INTO msg_participants "
        "(id, conversation_id, user_id, role, joined_at) VALUES (?, ?, ?, 'member', ?)",
        (generate_snowflake_id(), cid, uid, NOW),
    )
    return cid


def _message(db, cid, author_id):
    mid = generate_snowflake_id()
    db.execute(
        "INSERT INTO msg_messages "
        "(id, conversation_id, author_id, content, created_at, updated_at) "
        "VALUES (?, ?, ?, 'private body', ?, ?)",
        (mid, cid, author_id, NOW, NOW),
    )
    return mid


def _notification(db, recipient, sender, mid, cid, preview):
    nid = generate_snowflake_id()
    db.execute(
        "INSERT INTO notif_notifications "
        "(id, user_id, sender_id, message_id, conversation_id, content_preview, "
        " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (nid, recipient, sender, mid, cid, preview, NOW),
    )
    return nid


def test_erase_scrubs_notification_presence_profile_and_tracking_rows(
    db, auth_manager, pii_gen, monkeypatch
):
    monkeypatch.setattr("src.core.erasure._media_manager", lambda: None)
    user = _register(auth_manager, pii_gen, "eraseres")
    other = _register(auth_manager, pii_gen, "eraseoth")
    uid, oid = user.id, other.id

    cid = _conversation(db, uid)
    own_mid = _message(db, cid, uid)
    other_mid = _message(db, cid, oid)

    # Three links: user's own feed, other's feed about user's message, and
    # other's feed for a notification the user sent.
    n1 = _notification(db, uid, oid, own_mid, cid, "own preview")
    n2 = _notification(db, oid, uid, own_mid, cid, "about user preview")
    n3 = _notification(db, oid, uid, other_mid, cid, "sent by user preview")

    db.execute(
        "INSERT INTO notif_settings (id, user_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?)",
        (generate_snowflake_id(), uid, NOW, NOW),
    )
    db.execute(
        "INSERT INTO notif_channel_overrides "
        "(id, user_id, channel_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (generate_snowflake_id(), uid, 42, NOW, NOW),
    )
    db.execute(
        "INSERT INTO notif_unread (id, user_id, conversation_id, updated_at) "
        "VALUES (?, ?, ?, ?)",
        (generate_snowflake_id(), uid, cid, NOW),
    )

    db.execute(
        "INSERT INTO pres_presence (user_id, status, last_seen, updated_at) "
        "VALUES (?, 'online', ?, ?)",
        (uid, NOW, NOW),
    )
    db.execute(
        "INSERT INTO pres_custom_status (user_id, text, created_at) VALUES (?, 'busy', ?)",
        (uid, NOW),
    )
    db.execute(
        "INSERT INTO pres_activity "
        "(user_id, activity_type, name, created_at) VALUES (?, 'playing', 'chess', ?)",
        (uid, NOW),
    )
    db.execute(
        "INSERT INTO pres_typing "
        "(id, user_id, channel_id, started_at, expires_at) VALUES (?, ?, ?, ?, ?)",
        (generate_snowflake_id(), uid, 42, NOW, NOW + 60_000),
    )
    db.execute(
        "INSERT INTO user_profiles "
        "(user_id, bio, social_links, created_at, updated_at) "
        "VALUES (?, 'private bio', '[]', ?, ?)",
        (uid, NOW, NOW),
    )
    db.execute(
        "INSERT INTO push_tokens "
        "(id, user_id, token, platform, created_at, updated_at) "
        "VALUES (?, ?, 'device-token', 'ios', ?, ?)",
        (generate_snowflake_id(), uid, NOW, NOW),
    )
    db.execute(
        "INSERT INTO user_last_chat (id, user_id, conversation_id, updated_at) "
        "VALUES (?, ?, ?, ?)",
        (generate_snowflake_id(), uid, cid, NOW),
    )
    db.execute(
        "INSERT INTO user_recent_chats "
        "(id, user_id, conversation_id, accessed_at) VALUES (?, ?, ?, ?)",
        (generate_snowflake_id(), uid, cid, NOW),
    )

    result = erase_user_residue(db, uid)

    assert result["notifications"] == 3
    assert result["personal_rows"] >= 7
    placeholders = ",".join("?" * 3)
    assert (
        db.fetch_one(
            f"SELECT 1 FROM notif_notifications WHERE id IN ({placeholders})",
            (n1, n2, n3),
        )
        is None
    )
    for table, column in (
        ("notif_settings", "user_id"),
        ("notif_channel_overrides", "user_id"),
        ("notif_unread", "user_id"),
        ("pres_presence", "user_id"),
        ("pres_custom_status", "user_id"),
        ("pres_activity", "user_id"),
        ("pres_typing", "user_id"),
        ("user_profiles", "user_id"),
        ("push_tokens", "user_id"),
    ):
        assert (
            db.fetch_one(f"SELECT 1 FROM {table} WHERE {column} = ?", (uid,)) is None
        ), f"{table} residue survived erasure"


def test_delete_conversation_scrubs_notifications_and_tracking(
    db, auth_manager, pii_gen
):
    user = _register(auth_manager, pii_gen, "erase_conv")
    uid = user.id
    cid = _conversation(db, uid)
    mid = _message(db, cid, uid)
    nid = _notification(db, uid, uid, mid, cid, "preview")
    db.execute(
        "INSERT INTO user_last_chat (id, user_id, conversation_id, updated_at) "
        "VALUES (?, ?, ?, ?)",
        (generate_snowflake_id(), uid, cid, NOW),
    )
    db.execute(
        "INSERT INTO user_recent_chats "
        "(id, user_id, conversation_id, accessed_at) VALUES (?, ?, ?, ?)",
        (generate_snowflake_id(), uid, cid, NOW),
    )

    delete_conversation(db, cid)

    assert (
        db.fetch_one("SELECT 1 FROM notif_notifications WHERE id = ?", (nid,)) is None
    )
    assert (
        db.fetch_one("SELECT 1 FROM user_last_chat WHERE conversation_id = ?", (cid,))
        is None
    )
    assert (
        db.fetch_one(
            "SELECT 1 FROM user_recent_chats WHERE conversation_id = ?", (cid,)
        )
        is None
    )
