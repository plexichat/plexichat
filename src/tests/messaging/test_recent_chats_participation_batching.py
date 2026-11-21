"""Query-count regression for the hot recent-chats read path.

``GET /users/@me/recent-chats`` (``LastChatService.get_recent_chats``) used to
verify participation by calling ``is_participant`` once per row.  Each cold
call is a separate ``msg_participants`` probe (plus conversation-metadata and
server-membership fallbacks), so the query count grew linearly with the page
size even though the page is capped at ten conversations.

These tests measure the DB round-trips from a cold per-pair cache with two and
with ten recent chats.  The count must stay constant, and a conversation the
viewer is not a participant in must never be returned.
"""

import pytest

from src.core.messaging.services.last_chat import LastChatService

pytestmark = pytest.mark.messaging

# Ids well above the snowflakes minted by the other fixtures in a test.
_BASE_CONVERSATION_ID = 8_700_000_000_000_000
_FOREIGN_CONVERSATION_ID = 8_799_999_999_999_999


def _seed(db, user_id, count):
    """Insert recent-chat rows plus real participant rows from a cold state."""
    now = 1_700_000_000_000
    conversation_ids = []
    for index in range(count):
        cid = _BASE_CONVERSATION_ID + index
        db.execute(
            "INSERT INTO msg_conversations "
            "(id, conversation_type, name, owner_id, created_at, updated_at, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (cid, "group", f"recent-{index}", user_id, now, now, None),
        )
        db.execute(
            "INSERT INTO msg_participants "
            "(id, conversation_id, user_id, role, joined_at) VALUES (?, ?, ?, ?, ?)",
            (cid, cid, user_id, "member", now),
        )
        db.execute(
            "INSERT INTO user_recent_chats "
            "(id, user_id, conversation_id, accessed_at, unread_count) "
            "VALUES (?, ?, ?, ?, ?)",
            (cid, user_id, cid, now + index, 0),
        )
        conversation_ids.append(cid)

    # A recent-chat row for a conversation the viewer cannot see.  It must be
    # filtered out on every read, including when participation is resolved in
    # bulk rather than row by row.
    db.execute(
        "INSERT INTO msg_conversations "
        "(id, conversation_type, name, owner_id, created_at, updated_at, metadata) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            _FOREIGN_CONVERSATION_ID,
            "group",
            "foreign",
            user_id,
            now,
            now,
            None,
        ),
    )
    db.execute(
        "INSERT INTO user_recent_chats "
        "(id, user_id, conversation_id, accessed_at, unread_count) "
        "VALUES (?, ?, ?, ?, ?)",
        (_FOREIGN_CONVERSATION_ID, user_id, _FOREIGN_CONVERSATION_ID, now + 500, 0),
    )
    return conversation_ids


def _cold_read(svc, user_id, limit):
    """Run one recent-chats read from a cold cache and return (count, rows)."""
    from src.core.database.cache.base import mem_cache_clear_pattern

    mem_cache_clear_pattern("*")
    participant_svc = svc._participant_svc
    if hasattr(participant_svc, "_cache"):
        participant_svc._cache.clear()
    db = svc._db
    # The Database keeps a short-lived result cache keyed by SQL + params;
    # clear it so the measurement reflects real round-trips, not cache hits.
    with db._query_cache_lock:
        db._query_cache.clear()
    db.reset_request_metrics()
    rows = svc.get_recent_chats(user_id, limit=limit)
    return db.get_request_metrics()["query_count"], rows


def test_recent_chats_query_count_is_independent_of_page_size(
    db, messaging_manager, test_user
):
    user_id = test_user.id
    svc = LastChatService(db, messaging_manager._participant_svc)

    seeded_ids = _seed(db, user_id, 10)
    two_queries, two_rows = _cold_read(svc, user_id, limit=2)
    ten_queries, ten_rows = _cold_read(svc, user_id, limit=10)

    # The foreign conversation is the most recently accessed row, so it is at
    # the top of both pages; it must be filtered out on every read.
    assert len(two_rows) == 1
    assert int(two_rows[0]["conversation_id"]) in seeded_ids
    returned = {int(row["conversation_id"]) for row in ten_rows}
    assert _FOREIGN_CONVERSATION_ID not in returned
    assert len(returned) == 9
    assert returned.issubset(set(seeded_ids))

    print(
        f"\nRECENT_CHATS_QUERY_COUNT small_page={two_queries} large_page={ten_queries}"
    )
    assert ten_queries <= two_queries + 1, (
        "recent-chats participation check reverted to an N+1: "
        f"2 rows -> {two_queries} queries, 10 rows -> {ten_queries}"
    )
