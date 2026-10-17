"""Negative listing limits must return an empty page, not the whole table.

SQLite treats ``LIMIT -1`` as *unlimited*, so a negative ``limit`` passed to
the relationships listing methods would bypass the page cap and dump every
row. The manager clamps from below (``max(0, limit)``) so a negative limit
yields ``LIMIT 0`` — an empty page — while every non-negative limit behaves
exactly as before.
"""


class TestNegativeListingLimitClamp:
    def _make_friends(self, rel_manager, main_user, others):
        for other in others:
            request = rel_manager.send_friend_request(main_user.id, other.id)
            rel_manager.accept_friend_request(other.id, request.id)

    def test_get_friends_negative_limit_returns_empty(self, rel_manager, three_users):
        user1, user2, user3 = three_users
        self._make_friends(rel_manager, user1, (user2, user3))

        assert len(rel_manager.get_friends(user1.id)) == 2
        assert rel_manager.get_friends(user1.id, limit=-1) == []

    def test_pending_incoming_negative_limit_returns_empty(
        self, rel_manager, two_users
    ):
        user1, user2 = two_users
        rel_manager.send_friend_request(user2.id, user1.id)

        assert len(rel_manager.get_pending_requests_incoming(user1.id)) == 1
        assert rel_manager.get_pending_requests_incoming(user1.id, limit=-1) == []

    def test_pending_outgoing_negative_limit_returns_empty(
        self, rel_manager, two_users
    ):
        user1, user2 = two_users
        rel_manager.send_friend_request(user1.id, user2.id)

        assert len(rel_manager.get_pending_requests_outgoing(user1.id)) == 1
        assert rel_manager.get_pending_requests_outgoing(user1.id, limit=-1) == []

    def test_blocked_users_negative_limit_returns_empty(self, rel_manager, two_users):
        user1, user2 = two_users
        rel_manager.block_user(user1.id, user2.id)

        assert len(rel_manager.get_blocked_users(user1.id)) == 1
        assert rel_manager.get_blocked_users(user1.id, limit=-1) == []

    def test_suggested_friends_negative_limit_returns_empty(
        self, rel_manager, three_users
    ):
        user1, user2, user3 = three_users
        req1 = rel_manager.send_friend_request(user1.id, user2.id)
        req2 = rel_manager.send_friend_request(user2.id, user3.id)
        rel_manager.accept_friend_request(user2.id, req1.id)
        rel_manager.accept_friend_request(user3.id, req2.id)

        assert user3.id in rel_manager.get_suggested_friends(user1.id)
        assert rel_manager.get_suggested_friends(user1.id, limit=-1) == []

    def test_non_negative_limits_behave_as_before(self, rel_manager, three_users):
        user1, user2, user3 = three_users
        self._make_friends(rel_manager, user1, (user2, user3))

        assert len(rel_manager.get_friends(user1.id, limit=1)) == 1
        assert len(rel_manager.get_friends(user1.id, limit=100)) == 2
        assert rel_manager.get_friends(user1.id, limit=0) == []
