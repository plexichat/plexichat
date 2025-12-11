"""
Tests for presence integration with relationships and servers.
"""

from src.core.presence import UserStatus


class TestOnlineFriends:
    """Tests for getting online friends."""

    def test_get_online_friends_success(self, friends_pair):
        """Test getting online friends."""
        user1, user2, relationships, presence = friends_pair

        presence.set_status(user2.id, UserStatus.ONLINE)

        online = presence.get_online_friends(user1.id)

        assert user2.id in online

    def test_get_online_friends_empty(self, friends_pair):
        """Test getting online friends when none online."""
        user1, user2, relationships, presence = friends_pair

        presence.set_status(user2.id, UserStatus.OFFLINE)

        online = presence.get_online_friends(user1.id)

        assert user2.id not in online

    def test_get_online_friends_includes_idle(self, friends_pair):
        """Test that idle friends are included in online."""
        user1, user2, relationships, presence = friends_pair

        presence.set_status(user2.id, UserStatus.IDLE)

        online = presence.get_online_friends(user1.id)

        assert user2.id in online

    def test_get_online_friends_includes_dnd(self, friends_pair):
        """Test that dnd friends are included in online."""
        user1, user2, relationships, presence = friends_pair

        presence.set_status(user2.id, UserStatus.DND)

        online = presence.get_online_friends(user1.id)

        assert user2.id in online

    def test_get_online_friends_excludes_invisible(self, friends_pair):
        """Test that invisible friends are excluded from online."""
        user1, user2, relationships, presence = friends_pair

        presence.set_status(user2.id, UserStatus.INVISIBLE)

        online = presence.get_online_friends(user1.id)

        # Invisible users are not in the online list
        assert user2.id not in online

    def test_get_online_friends_no_friends(self, fresh_users):
        """Test getting online friends when no friends."""
        user1, user2, presence = fresh_users

        online = presence.get_online_friends(user1.id)

        assert len(online) == 0

    def test_get_online_friends_multiple(self, db_and_modules):
        """Test getting multiple online friends."""
        db, auth, servers, relationships, presence = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:6]

        main_user = auth.register(
            username=f"main_{unique_id}",
            email=f"main_{unique_id}@example.com",
            password="TestPass123!"
        )

        online_friends = []
        for i in range(3):
            friend = auth.register(
                username=f"friend_{unique_id}_{i}",
                email=f"friend_{unique_id}_{i}@example.com",
                password="TestPass123!"
            )
            req = relationships.send_friend_request(main_user.id, friend.id)
            relationships.accept_friend_request(friend.id, req.id)
            presence.set_status(friend.id, UserStatus.ONLINE)
            online_friends.append(friend.id)

        online = presence.get_online_friends(main_user.id)

        for friend_id in online_friends:
            assert friend_id in online


class TestOnlineServerMembers:
    """Tests for getting online server members."""

    def test_get_online_server_members_success(self, users_with_server):
        """Test getting online server members."""
        user1, user2, user3, server, servers, presence = users_with_server

        presence.set_status(user2.id, UserStatus.ONLINE)

        online = presence.get_online_server_members(user1.id, server.id)

        assert user2.id in online

    def test_get_online_server_members_empty(self, users_with_server):
        """Test getting online members when none online."""
        user1, user2, user3, server, servers, presence = users_with_server

        presence.set_status(user2.id, UserStatus.OFFLINE)
        presence.set_status(user3.id, UserStatus.OFFLINE)

        online = presence.get_online_server_members(user1.id, server.id)

        assert user2.id not in online
        assert user3.id not in online

    def test_get_online_server_members_includes_idle(self, users_with_server):
        """Test that idle members are included."""
        user1, user2, user3, server, servers, presence = users_with_server

        presence.set_status(user2.id, UserStatus.IDLE)

        online = presence.get_online_server_members(user1.id, server.id)

        assert user2.id in online

    def test_get_online_server_members_includes_dnd(self, users_with_server):
        """Test that dnd members are included."""
        user1, user2, user3, server, servers, presence = users_with_server

        presence.set_status(user2.id, UserStatus.DND)

        online = presence.get_online_server_members(user1.id, server.id)

        assert user2.id in online

    def test_get_online_server_members_excludes_invisible(self, users_with_server):
        """Test that invisible members are excluded."""
        user1, user2, user3, server, servers, presence = users_with_server

        presence.set_status(user2.id, UserStatus.INVISIBLE)

        online = presence.get_online_server_members(user1.id, server.id)

        assert user2.id not in online

    def test_get_online_server_members_multiple(self, users_with_server):
        """Test getting multiple online members."""
        user1, user2, user3, server, servers, presence = users_with_server

        presence.set_status(user2.id, UserStatus.ONLINE)
        presence.set_status(user3.id, UserStatus.ONLINE)

        online = presence.get_online_server_members(user1.id, server.id)

        assert user2.id in online
        assert user3.id in online


class TestBulkPresence:
    """Tests for bulk presence queries."""

    def test_get_presences_success(self, db_and_modules):
        """Test getting multiple presences."""
        db, auth, servers, relationships, presence = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:6]

        users = []
        for i in range(3):
            user = auth.register(
                username=f"bulk_{unique_id}_{i}",
                email=f"bulk_{unique_id}_{i}@example.com",
                password="TestPass123!"
            )
            presence.set_status(user.id, UserStatus.ONLINE)
            users.append(user)

        user_ids = [u.id for u in users]
        presences = presence.get_presences(user_ids)

        assert len(presences) == 3
        for pres in presences:
            assert pres.status == UserStatus.ONLINE

    def test_get_presences_empty_list(self, fresh_users):
        """Test getting presences with empty list."""
        user1, user2, presence = fresh_users

        presences = presence.get_presences([])

        assert len(presences) == 0

    def test_get_presences_mixed_statuses(self, db_and_modules):
        """Test getting presences with mixed statuses."""
        db, auth, servers, relationships, presence = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:6]

        user1 = auth.register(
            username=f"mix1_{unique_id}",
            email=f"mix1_{unique_id}@example.com",
            password="TestPass123!"
        )
        user2 = auth.register(
            username=f"mix2_{unique_id}",
            email=f"mix2_{unique_id}@example.com",
            password="TestPass123!"
        )
        user3 = auth.register(
            username=f"mix3_{unique_id}",
            email=f"mix3_{unique_id}@example.com",
            password="TestPass123!"
        )

        presence.set_status(user1.id, UserStatus.ONLINE)
        presence.set_status(user2.id, UserStatus.IDLE)
        presence.set_status(user3.id, UserStatus.DND)

        presences = presence.get_presences([user1.id, user2.id, user3.id])

        statuses = {p.user_id: p.status for p in presences}
        assert statuses[user1.id] == UserStatus.ONLINE
        assert statuses[user2.id] == UserStatus.IDLE
        assert statuses[user3.id] == UserStatus.DND


class TestLastSeen:
    """Tests for last seen functionality."""

    def test_update_last_seen(self, fresh_users):
        """Test updating last seen timestamp."""
        user1, user2, presence = fresh_users

        result = presence.update_last_seen(user1.id)

        assert result.last_seen > 0

    def test_last_seen_updates_on_status_change(self, fresh_users):
        """Test that last seen updates on status change."""
        user1, user2, presence = fresh_users

        result1 = presence.set_status(user1.id, UserStatus.ONLINE)
        import time
        time.sleep(0.01)
        result2 = presence.set_status(user1.id, UserStatus.IDLE)

        assert result2.last_seen >= result1.last_seen

    def test_last_seen_in_presence(self, fresh_users):
        """Test that last seen is included in presence."""
        user1, user2, presence = fresh_users

        presence.set_status(user1.id, UserStatus.ONLINE)
        pres = presence.get_presence(user1.id)

        assert pres.last_seen > 0


class TestPresenceWithActivity:
    """Tests for presence with activity integration."""

    def test_presence_includes_activity(self, fresh_users):
        """Test that presence includes activity."""
        user1, user2, presence = fresh_users

        presence.set_status(user1.id, UserStatus.ONLINE)
        presence.set_activity(user1.id, presence.ActivityType.PLAYING, "Game")

        pres = presence.get_presence(user1.id)

        assert pres.activity is not None
        assert pres.activity.name == "Game"

    def test_presence_includes_custom_status(self, fresh_users):
        """Test that presence includes custom status."""
        user1, user2, presence = fresh_users

        presence.set_status(user1.id, UserStatus.ONLINE)
        presence.set_custom_status(user1.id, "Working")

        pres = presence.get_presence(user1.id)

        assert pres.custom_status is not None
        assert pres.custom_status.text == "Working"

    def test_full_presence_data(self, fresh_users):
        """Test getting full presence with all data."""
        user1, user2, presence = fresh_users

        presence.set_status(user1.id, UserStatus.ONLINE)
        presence.set_custom_status(user1.id, "Coding", emoji=":computer:")
        presence.set_activity(
            user1.id,
            presence.ActivityType.PLAYING,
            "VS Code",
            details="Writing tests"
        )

        pres = presence.get_presence(user1.id)

        assert pres.status == UserStatus.ONLINE
        assert pres.custom_status.text == "Coding"
        assert pres.custom_status.emoji == ":computer:"
        assert pres.activity.name == "VS Code"
        assert pres.activity.details == "Writing tests"
        assert pres.last_seen > 0
        assert pres.updated_at > 0
