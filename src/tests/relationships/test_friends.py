"""
Tests for friends list and mutual friends functionality.
"""

import pytest
from src.core.relationships import (
    RelationshipStatus,
    NotFriendsError,
)


class TestGetFriends:
    """Tests for getting friends list."""

    def test_get_friends_success(self, friends_pair):
        """Test getting friends list."""
        user1, user2, relationships = friends_pair

        friends = relationships.get_friends(user1.id)

        assert len(friends) == 1
        assert friends[0].friend_id == user2.id

    def test_get_friends_empty(self, users):
        """Test getting friends when none exist."""
        user1, user2, user3, user4, relationships = users

        friends = relationships.get_friends(user4.id)

        assert len(friends) == 0

    def test_get_friends_bidirectional(self, friends_pair):
        """Test that friendship is bidirectional."""
        user1, user2, relationships = friends_pair

        friends1 = relationships.get_friends(user1.id)
        friends2 = relationships.get_friends(user2.id)

        assert len(friends1) == 1
        assert len(friends2) == 1
        assert friends1[0].friend_id == user2.id
        assert friends2[0].friend_id == user1.id

    def test_get_friend_ids(self, friends_pair):
        """Test getting friend IDs."""
        user1, user2, relationships = friends_pair

        friend_ids = relationships.get_friend_ids(user1.id)

        assert user2.id in friend_ids

    def test_get_friends_with_limit(self, db_and_modules):
        """Test getting friends with limit."""
        db, auth, servers, relationships = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:6]

        # Create main user
        main_user = auth.register(
            username=f"main_{unique_id}",
            email=f"main_{unique_id}@example.com",
            password="TestPass123!",
        )

        # Create multiple friends
        for i in range(5):
            friend = auth.register(
                username=f"friend_{unique_id}_{i}",
                email=f"friend_{unique_id}_{i}@example.com",
                password="TestPass123!",
            )
            request = relationships.send_friend_request(main_user.id, friend.id)
            relationships.accept_friend_request(friend.id, request.id)

        # Get with limit
        friends = relationships.get_friends(main_user.id, limit=3)

        assert len(friends) == 3


class TestRemoveFriend:
    """Tests for removing friends."""

    def test_remove_friend_success(self, friends_pair):
        """Test removing a friend successfully."""
        user1, user2, relationships = friends_pair

        result = relationships.remove_friend(user1.id, user2.id)

        assert result is True

        # Verify friendship is removed
        rel = relationships.get_relationship(user1.id, user2.id)
        assert rel.status == RelationshipStatus.NONE

    def test_remove_friend_bidirectional(self, friends_pair):
        """Test that removing friend is bidirectional."""
        user1, user2, relationships = friends_pair

        relationships.remove_friend(user1.id, user2.id)

        # Both sides should show no friendship
        friends1 = relationships.get_friend_ids(user1.id)
        friends2 = relationships.get_friend_ids(user2.id)

        assert user2.id not in friends1
        assert user1.id not in friends2

    def test_remove_non_friend_fails(self, fresh_users):
        """Test that removing a non-friend fails."""
        user1, user2, relationships = fresh_users

        with pytest.raises(NotFriendsError):
            relationships.remove_friend(user1.id, user2.id)

    def test_remove_friend_allows_new_request(self, friends_pair):
        """Test that removing friend allows new friend request."""
        user1, user2, relationships = friends_pair

        relationships.remove_friend(user1.id, user2.id)

        # Should be able to send new request
        request = relationships.send_friend_request(user1.id, user2.id)
        assert request is not None


class TestMutualFriends:
    """Tests for mutual friends functionality."""

    def test_get_mutual_friends_success(self, db_and_modules):
        """Test getting mutual friends."""
        db, auth, servers, relationships = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:6]

        # Create users
        user1 = auth.register(
            username=f"mut1_{unique_id}",
            email=f"mut1_{unique_id}@example.com",
            password="TestPass123!",
        )
        user2 = auth.register(
            username=f"mut2_{unique_id}",
            email=f"mut2_{unique_id}@example.com",
            password="TestPass123!",
        )
        mutual = auth.register(
            username=f"mutual_{unique_id}",
            email=f"mutual_{unique_id}@example.com",
            password="TestPass123!",
        )

        # Make mutual friend with both
        req1 = relationships.send_friend_request(user1.id, mutual.id)
        relationships.accept_friend_request(mutual.id, req1.id)

        req2 = relationships.send_friend_request(user2.id, mutual.id)
        relationships.accept_friend_request(mutual.id, req2.id)

        # Get mutual friends
        mutual_friends = relationships.get_mutual_friends(user1.id, user2.id)

        assert mutual.id in mutual_friends

    def test_get_mutual_friends_empty(self, fresh_users):
        """Test getting mutual friends when none exist."""
        user1, user2, relationships = fresh_users

        mutual_friends = relationships.get_mutual_friends(user1.id, user2.id)

        assert len(mutual_friends) == 0

    def test_get_mutual_friend_count(self, db_and_modules):
        """Test getting mutual friend count."""
        db, auth, servers, relationships = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:6]

        # Create users
        user1 = auth.register(
            username=f"cnt1_{unique_id}",
            email=f"cnt1_{unique_id}@example.com",
            password="TestPass123!",
        )
        user2 = auth.register(
            username=f"cnt2_{unique_id}",
            email=f"cnt2_{unique_id}@example.com",
            password="TestPass123!",
        )

        # Create multiple mutual friends
        for i in range(3):
            mutual = auth.register(
                username=f"mutcnt_{unique_id}_{i}",
                email=f"mutcnt_{unique_id}_{i}@example.com",
                password="TestPass123!",
            )
            req1 = relationships.send_friend_request(user1.id, mutual.id)
            relationships.accept_friend_request(mutual.id, req1.id)
            req2 = relationships.send_friend_request(user2.id, mutual.id)
            relationships.accept_friend_request(mutual.id, req2.id)

        count = relationships.get_mutual_friend_count(user1.id, user2.id)

        assert count == 3

    def test_mutual_friends_symmetric(self, db_and_modules):
        """Test that mutual friends is symmetric."""
        db, auth, servers, relationships = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:6]

        user1 = auth.register(
            username=f"sym1_{unique_id}",
            email=f"sym1_{unique_id}@example.com",
            password="TestPass123!",
        )
        user2 = auth.register(
            username=f"sym2_{unique_id}",
            email=f"sym2_{unique_id}@example.com",
            password="TestPass123!",
        )
        mutual = auth.register(
            username=f"symmut_{unique_id}",
            email=f"symmut_{unique_id}@example.com",
            password="TestPass123!",
        )

        req1 = relationships.send_friend_request(user1.id, mutual.id)
        relationships.accept_friend_request(mutual.id, req1.id)
        req2 = relationships.send_friend_request(user2.id, mutual.id)
        relationships.accept_friend_request(mutual.id, req2.id)

        # Both directions should give same result
        mutual1 = relationships.get_mutual_friends(user1.id, user2.id)
        mutual2 = relationships.get_mutual_friends(user2.id, user1.id)

        assert set(mutual1) == set(mutual2)


class TestMutualServers:
    """Tests for mutual servers functionality."""

    def test_get_mutual_servers_success(self, users_with_server):
        """Test getting mutual servers."""
        user1, user2, user3, server, servers, relationships = users_with_server

        mutual_servers = relationships.get_mutual_servers(user1.id, user2.id)

        assert server.id in mutual_servers

    def test_get_mutual_servers_empty(self, users_with_server):
        """Test getting mutual servers when none exist."""
        user1, user2, user3, server, servers, relationships = users_with_server

        # User3 is not in the server
        mutual_servers = relationships.get_mutual_servers(user1.id, user3.id)

        assert len(mutual_servers) == 0

    def test_get_mutual_server_count(self, users_with_server):
        """Test getting mutual server count."""
        user1, user2, user3, server, servers, relationships = users_with_server

        count = relationships.get_mutual_server_count(user1.id, user2.id)

        assert count >= 1

    def test_get_mutual_info(self, users_with_server):
        """Test getting all mutual info at once."""
        user1, user2, user3, server, servers, relationships = users_with_server

        info = relationships.get_mutual_info(user1.id, user2.id)

        assert info.mutual_server_count >= 1
        assert server.id in info.mutual_servers
        assert info.mutual_friend_count == len(info.mutual_friends)
