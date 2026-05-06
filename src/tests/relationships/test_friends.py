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

    def test_get_friends_success(self, rel_manager, two_users):
        """Test getting friends list."""
        user1, user2 = two_users

        # Create friendship
        request = rel_manager.send_friend_request(user1.id, user2.id)
        rel_manager.accept_friend_request(user2.id, request.id)

        friends = rel_manager.get_friends(user1.id)

        assert len(friends) == 1
        assert friends[0].friend_id == user2.id

    def test_get_friends_empty(self, rel_manager, three_users):
        """Test getting friends when none exist."""
        user1, user2, user3 = three_users

        friends = rel_manager.get_friends(user3.id)

        assert len(friends) == 0

    def test_get_friends_bidirectional(self, rel_manager, two_users):
        """Test that friendship is bidirectional."""
        user1, user2 = two_users

        # Create friendship
        request = rel_manager.send_friend_request(user1.id, user2.id)
        rel_manager.accept_friend_request(user2.id, request.id)

        friends1 = rel_manager.get_friends(user1.id)
        friends2 = rel_manager.get_friends(user2.id)

        assert len(friends1) == 1
        assert len(friends2) == 1
        assert friends1[0].friend_id == user2.id
        assert friends2[0].friend_id == user1.id

    def test_get_friend_ids(self, rel_manager, two_users):
        """Test getting friend IDs."""
        user1, user2 = two_users

        # Create friendship
        request = rel_manager.send_friend_request(user1.id, user2.id)
        rel_manager.accept_friend_request(user2.id, request.id)

        friend_ids = rel_manager.get_friend_ids(user1.id)

        assert user2.id in friend_ids

    def test_get_friends_with_limit(self, db, auth_manager, rel_manager):
        """Test getting friends with limit."""
        from unittest.mock import patch
        from src.utils import encryption
        import uuid

        unique_id = uuid.uuid4().hex[:6]

        # Create main user
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            main_user = auth_manager.register(
                username=f"main_{unique_id}",
                email=f"main_{unique_id}@example.com",
                password="TestPass123!",
            )

            # Create multiple friends
            for i in range(5):
                friend = auth_manager.register(
                    username=f"friend_{unique_id}_{i}",
                    email=f"friend_{unique_id}_{i}@example.com",
                    password="TestPass123!",
                )
                request = rel_manager.send_friend_request(main_user.id, friend.id)
                rel_manager.accept_friend_request(friend.id, request.id)

        # Get with limit
        friends = rel_manager.get_friends(main_user.id, limit=3)

        assert len(friends) == 3


class TestRemoveFriend:
    """Tests for removing friends."""

    def test_remove_friend_success(self, rel_manager, two_users):
        """Test removing a friend successfully."""
        user1, user2 = two_users

        # Create friendship
        request = rel_manager.send_friend_request(user1.id, user2.id)
        rel_manager.accept_friend_request(user2.id, request.id)

        result = rel_manager.remove_friend(user1.id, user2.id)

        assert result is True

        # Verify friendship is removed
        rel = rel_manager.get_relationship(user1.id, user2.id)
        assert rel.status == RelationshipStatus.NONE

    def test_remove_friend_bidirectional(self, rel_manager, two_users):
        """Test that removing friend is bidirectional."""
        user1, user2 = two_users

        # Create friendship
        request = rel_manager.send_friend_request(user1.id, user2.id)
        rel_manager.accept_friend_request(user2.id, request.id)

        rel_manager.remove_friend(user1.id, user2.id)

        # Both sides should show no friendship
        friends1 = rel_manager.get_friend_ids(user1.id)
        friends2 = rel_manager.get_friend_ids(user2.id)

        assert user2.id not in friends1
        assert user1.id not in friends2

    def test_remove_non_friend_fails(self, rel_manager, two_users):
        """Test that removing a non-friend fails."""
        user1, user2 = two_users

        with pytest.raises(NotFriendsError):
            rel_manager.remove_friend(user1.id, user2.id)

    def test_remove_friend_allows_new_request(self, rel_manager, two_users):
        """Test that removing friend allows new friend request."""
        user1, user2 = two_users

        # Create friendship
        request = rel_manager.send_friend_request(user1.id, user2.id)
        rel_manager.accept_friend_request(user2.id, request.id)

        rel_manager.remove_friend(user1.id, user2.id)

        # Should be able to send new request
        request = rel_manager.send_friend_request(user1.id, user2.id)
        assert request is not None


class TestMutualFriends:
    """Tests for mutual friends functionality."""

    def test_get_mutual_friends_success(self, db, auth_manager, rel_manager):
        """Test getting mutual friends."""
        from unittest.mock import patch
        from src.utils import encryption
        import uuid

        unique_id = uuid.uuid4().hex[:6]

        # Create users
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username=f"mut1_{unique_id}",
                email=f"mut1_{unique_id}@example.com",
                password="TestPass123!",
            )
            user2 = auth_manager.register(
                username=f"mut2_{unique_id}",
                email=f"mut2_{unique_id}@example.com",
                password="TestPass123!",
            )
            mutual = auth_manager.register(
                username=f"mutual_{unique_id}",
                email=f"mutual_{unique_id}@example.com",
                password="TestPass123!",
            )

            # Make mutual friend with both
            req1 = rel_manager.send_friend_request(user1.id, mutual.id)
            rel_manager.accept_friend_request(mutual.id, req1.id)

            req2 = rel_manager.send_friend_request(user2.id, mutual.id)
            rel_manager.accept_friend_request(mutual.id, req2.id)

        # Get mutual friends
        mutual_friends = rel_manager.get_mutual_friends(user1.id, user2.id)

        assert mutual.id in mutual_friends

    def test_get_mutual_friends_empty(self, rel_manager, two_users):
        """Test getting mutual friends when none exist."""
        user1, user2 = two_users

        mutual_friends = rel_manager.get_mutual_friends(user1.id, user2.id)

        assert len(mutual_friends) == 0

    def test_get_mutual_friend_count(self, db, auth_manager, rel_manager):
        """Test getting mutual friend count."""
        from unittest.mock import patch
        from src.utils import encryption
        import uuid

        unique_id = uuid.uuid4().hex[:6]

        # Create users
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username=f"cnt1_{unique_id}",
                email=f"cnt1_{unique_id}@example.com",
                password="TestPass123!",
            )
            user2 = auth_manager.register(
                username=f"cnt2_{unique_id}",
                email=f"cnt2_{unique_id}@example.com",
                password="TestPass123!",
            )

            # Create multiple mutual friends
            for i in range(3):
                mutual = auth_manager.register(
                    username=f"mutcnt_{unique_id}_{i}",
                    email=f"mutcnt_{unique_id}_{i}@example.com",
                    password="TestPass123!",
                )
                req1 = rel_manager.send_friend_request(user1.id, mutual.id)
                rel_manager.accept_friend_request(mutual.id, req1.id)
                req2 = rel_manager.send_friend_request(user2.id, mutual.id)
                rel_manager.accept_friend_request(mutual.id, req2.id)

        count = rel_manager.get_mutual_friend_count(user1.id, user2.id)

        assert count == 3

    def test_mutual_friends_symmetric(self, db, auth_manager, rel_manager):
        """Test that mutual friends is symmetric."""
        from unittest.mock import patch
        from src.utils import encryption
        import uuid

        unique_id = uuid.uuid4().hex[:6]

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username=f"sym1_{unique_id}",
                email=f"sym1_{unique_id}@example.com",
                password="TestPass123!",
            )
            user2 = auth_manager.register(
                username=f"sym2_{unique_id}",
                email=f"sym2_{unique_id}@example.com",
                password="TestPass123!",
            )
            mutual = auth_manager.register(
                username=f"symmut_{unique_id}",
                email=f"symmut_{unique_id}@example.com",
                password="TestPass123!",
            )

            req1 = rel_manager.send_friend_request(user1.id, mutual.id)
            rel_manager.accept_friend_request(mutual.id, req1.id)
            req2 = rel_manager.send_friend_request(user2.id, mutual.id)
            rel_manager.accept_friend_request(mutual.id, req2.id)

        # Both directions should give same result
        mutual1 = rel_manager.get_mutual_friends(user1.id, user2.id)
        mutual2 = rel_manager.get_mutual_friends(user2.id, user1.id)

        assert set(mutual1) == set(mutual2)


class TestMutualServers:
    """Tests for mutual servers functionality."""

    def test_get_mutual_servers_success(self, db, server_manager, three_users):
        """Test getting mutual servers."""
        from src.core.relationships.manager import RelationshipManager

        user1, user2, user3 = three_users

        # Create a server and add users
        server = server_manager.create_server(user1.id, "Test Server")
        server_manager.add_member(server.id, user2.id)

        # Create rel_manager with server_manager
        rel_manager = RelationshipManager(db, servers_module=server_manager)

        mutual_servers = rel_manager.get_mutual_servers(user1.id, user2.id)

        assert server.id in mutual_servers

    def test_get_mutual_servers_empty(self, db, server_manager, three_users):
        """Test getting mutual servers when none exist."""
        from src.core.relationships.manager import RelationshipManager

        user1, user2, user3 = three_users

        # Create a server and add only user1
        server_manager.create_server(user1.id, "Test Server")

        # Create rel_manager with server_manager
        rel_manager = RelationshipManager(db, servers_module=server_manager)

        # User3 is not in the server
        mutual_servers = rel_manager.get_mutual_servers(user1.id, user3.id)

        assert len(mutual_servers) == 0

    def test_get_mutual_server_count(self, db, server_manager, three_users):
        """Test getting mutual server count."""
        from src.core.relationships.manager import RelationshipManager

        user1, user2, user3 = three_users

        # Create a server and add users
        server = server_manager.create_server(user1.id, "Test Server")
        server_manager.add_member(server.id, user2.id)

        # Create rel_manager with server_manager
        rel_manager = RelationshipManager(db, servers_module=server_manager)

        count = rel_manager.get_mutual_server_count(user1.id, user2.id)

        assert count >= 1

    def test_get_mutual_info(self, db, server_manager, three_users):
        """Test getting all mutual info at once."""
        from src.core.relationships.manager import RelationshipManager

        user1, user2, user3 = three_users

        # Create a server and add users
        server = server_manager.create_server(user1.id, "Test Server")
        server_manager.add_member(server.id, user2.id)

        # Create rel_manager with server_manager
        rel_manager = RelationshipManager(db, servers_module=server_manager)

        info = rel_manager.get_mutual_info(user1.id, user2.id)

        assert info.mutual_server_count >= 1
        assert server.id in info.mutual_servers
        assert info.mutual_friend_count == len(info.mutual_friends)
