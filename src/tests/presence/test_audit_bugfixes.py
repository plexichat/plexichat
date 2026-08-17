"""
Regression tests for additional presence audit fixes.

- P1: an expired custom status cached in Valkey must not be served on a
  cache-hit ``get_presence`` read (it must be evicted and re-read from the
  DB, where ``_cleanup_expired_custom_status`` purges it).
- P2: a custom status consisting of an emoji only (empty text) must be
  persisted and surfaced, not silently discarded.
"""

import time

import pytest

pytestmark = pytest.mark.integration


def _insert_expired_custom_status(db, user_id):
    past = int(time.time() * 1000) - 10_000
    db.execute("DELETE FROM pres_custom_status WHERE user_id = ?", (user_id,))
    db.execute(
        "INSERT INTO pres_custom_status (user_id, text, emoji, expires_at, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, "expired", None, past, past),
    )


def test_cache_hit_does_not_serve_expired_custom_status(fresh_users, monkeypatch):
    user1, _user2, presence_manager = fresh_users
    past = int(time.time() * 1000) - 10_000

    stale = {
        "user_id": user1.id,
        "status": "online",
        "last_seen": 0,
        "updated_at": 0,
        "custom_status": {
            "text": "expired",
            "emoji": None,
            "expires_at": past,
            "created_at": 0,
        },
        "activity": None,
        "current_channel_id": None,
        "current_server_id": None,
    }

    from src.core.presence.manager import presence as presence_module

    monkeypatch.setattr(presence_module, "get_cached_presence", lambda uid: stale)

    # DB also holds the expired status so the re-read path returns None.
    _insert_expired_custom_status(presence_manager._db, user1.id)

    pres = presence_manager.get_presence(user1.id, use_cache=True)
    assert pres.custom_status is None


def test_emoji_only_custom_status_is_persisted(fresh_users):
    user1, _user2, presence_manager = fresh_users
    out = presence_manager.set_custom_status(user_id=user1.id, text="", emoji=":wave:")
    assert out.custom_status is not None
    assert out.custom_status.emoji == ":wave:"

    pres = presence_manager.get_presence(user1.id)
    assert pres.custom_status is not None
    assert pres.custom_status.emoji == ":wave:"
    assert pres.custom_status.text == ""
