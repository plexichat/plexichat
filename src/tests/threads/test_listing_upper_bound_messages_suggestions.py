"""Regression tests: thread/suggestion listing limits must be clamped above.

``ThreadManager.get_messages`` and ``RelationshipManager.get_suggested_friends``
used ``limit = max(0, limit)``: SQLite reads a negative ``LIMIT ?`` as
*unlimited*, but a huge positive one materialized the entire table, and
``None``/bool/fraction/overflow input raised or was coerced permissively. Both
methods now use the shared fail-closed clamping pattern (cap 200).

Each test seeds more rows than the cap and first proves, via a direct SQL
count, that the data really exceeds one page. Without the fix the oversized
call would return every seeded row (and ``None`` would raise); with the fix it
returns exactly the cap, while small legitimate limits are unchanged.
"""

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.security]

CAP = 200
TOTAL = CAP + 25


class TestThreadMessagesUpperBound:
    def test_oversized_limit_is_capped_and_invalid_fails_closed(
        self, db, server_with_channel
    ):
        owner, _m1, _m2, _server, channel, _servers, threads = server_with_channel
        thread = threads.create_thread(
            user_id=owner.id, channel_id=channel.id, name="Upper bound clamp"
        )

        base = 10**7
        for i in range(TOTAL):
            mid = base + i
            db.execute(
                "INSERT INTO msg_messages "
                "(id, conversation_id, author_id, content, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (mid, thread.conversation_id, owner.id, f"msg {i}", 1000 + i, 1000 + i),
            )
            db.execute(
                "INSERT INTO thread_messages "
                "(id, thread_id, message_id, user_id, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (mid, thread.id, mid, owner.id, 1000 + i),
            )

        # The seeded thread really holds more than one page; otherwise the
        # oversized-limit assertion below would be vacuous.
        seeded = db.fetch_all(
            "SELECT COUNT(*) AS n FROM thread_messages WHERE thread_id = ?",
            (thread.id,),
        )
        assert seeded[0]["n"] == TOTAL > CAP

        assert len(threads.get_messages(owner.id, thread.id, limit=10**9)) == CAP
        assert len(threads.get_messages(owner.id, thread.id, limit=TOTAL)) == CAP
        # Legitimate small pages stay verbatim.
        assert len(threads.get_messages(owner.id, thread.id, limit=5)) == 5
        assert len(threads.get_messages(owner.id, thread.id)) == 50
        # Invalid / hostile coercion fails closed to an empty page.
        for bad in (None, True, False, 3.5, float("inf"), float("nan"), "1_0", "٤٢"):
            assert threads.get_messages(owner.id, thread.id, limit=bad) == []


class TestSuggestedFriendsUpperBound:
    def test_oversized_limit_is_capped_and_invalid_fails_closed(
        self, db, rel_manager, three_users
    ):
        viewer, hub, _other = three_users
        db.execute(
            "INSERT INTO rel_friends (user_id, friend_id, created_at) VALUES (?, ?, ?)",
            (viewer.id, hub.id, 1),
        )
        base = 2 * 10**7
        for i in range(TOTAL):
            candidate = base + i
            db.execute(
                "INSERT INTO auth_users "
                "(id, username, password_hash, permissions, created_at, updated_at) "
                "VALUES (?, ?, 'x', '[]', 1, 1)",
                (candidate, f"cand_{candidate}"),
            )
            db.execute(
                "INSERT INTO rel_friends (user_id, friend_id, created_at) "
                "VALUES (?, ?, ?)",
                (hub.id, candidate, 2 + i),
            )

        seeded = db.fetch_all(
            "SELECT COUNT(*) AS n FROM rel_friends WHERE user_id = ?",
            (hub.id,),
        )
        assert seeded[0]["n"] == TOTAL > CAP

        assert len(rel_manager.get_suggested_friends(viewer.id, limit=10**9)) == CAP
        assert len(rel_manager.get_suggested_friends(viewer.id, limit=TOTAL)) == CAP
        assert len(rel_manager.get_suggested_friends(viewer.id, limit=5)) == 5
        assert len(rel_manager.get_suggested_friends(viewer.id)) == 10
        for bad in (None, True, False, 3.5, float("inf"), float("nan"), "1_0", "٤٢"):
            assert rel_manager.get_suggested_friends(viewer.id, limit=bad) == []
