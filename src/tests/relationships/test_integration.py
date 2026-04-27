"""
Tests for integration with other modules (auth, servers, messaging).
"""

from src.core.relationships import (
    RelationshipStatus,
    FriendRequestStatus,
)


class TestAuthIntegration:
    """Tests for integration with auth module."""

    def test_relationship_with_valid_users(self, rel_manager, two_users):
        """Test that relationships work with valid auth users."""
        user1, user2 = two_users

        request = rel_manager.send_friend_request(user1.id, user2.id)

        assert request.sender_id == user1.id
        assert request.recipient_id == user2.id

    def test_get_relationship_with_auth_users(self, rel_manager, two_users):
        """Test getting relationship status with auth users."""
        user1, user2 = two_users

        rel = rel_manager.get_relationship(user1.id, user2.id)

        assert rel.user_id == user1.id
        assert rel.target_user_id == user2.id
        assert rel.status == RelationshipStatus.NONE


class TestServersIntegration:
    """Tests for integration with servers module."""

    def test_mutual_servers_with_server_members(self, db, server_manager, three_users):
        """Test mutual servers calculation with actual server members."""
        from src.core.relationships.manager import RelationshipManager

        user1, user2, user3 = three_users

        # Create a server and add users
        server = server_manager.create_server(user1.id, "Test Server")
        server_manager.add_member(server.id, user2.id)

        # Create rel_manager with server_manager
        rel_manager = RelationshipManager(db, servers_module=server_manager)

        mutual = rel_manager.get_mutual_servers(user1.id, user2.id)

        assert server.id in mutual

    def test_mutual_servers_excludes_non_members(self, db, server_manager, three_users):
        """Test that non-members are not included in mutual servers."""
        from src.core.relationships.manager import RelationshipManager

        user1, user2, user3 = three_users

        # Create a server and add only user1
        server = server_manager.create_server(user1.id, "Test Server")

        # Create rel_manager with server_manager
        rel_manager = RelationshipManager(db, servers_module=server_manager)

        # User3 is not in the server
        mutual = rel_manager.get_mutual_servers(user1.id, user3.id)

        assert server.id not in mutual

    def test_mutual_servers_multiple_servers(self, db, auth_manager, server_manager):
        """Test mutual servers with multiple shared servers."""
        from unittest.mock import patch
        from src.utils import encryption
        from src.core.relationships.manager import RelationshipManager
        import uuid

        unique_id = uuid.uuid4().hex[:6]

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username=f"multi1_{unique_id}",
                email=f"multi1_{unique_id}@example.com",
                password="TestPass123!",
            )
            user2 = auth_manager.register(
                username=f"multi2_{unique_id}",
                email=f"multi2_{unique_id}@example.com",
                password="TestPass123!",
            )

            # Create multiple servers
            server1 = server_manager.create_server(user1.id, f"Server1 {unique_id}")
            server2 = server_manager.create_server(user1.id, f"Server2 {unique_id}")

            # Add user2 to both
            server_manager.add_member(server1.id, user2.id)
            server_manager.add_member(server2.id, user2.id)

        # Create rel_manager with server_manager
        rel_manager = RelationshipManager(db, servers_module=server_manager)

        mutual = rel_manager.get_mutual_servers(user1.id, user2.id)

        assert server1.id in mutual
        assert server2.id in mutual
        assert len(mutual) >= 2


class TestRelationshipWorkflows:
    """Tests for complete relationship workflows."""

    def test_full_friend_request_workflow(self, rel_manager, two_users):
        """Test complete friend request workflow."""
        user1, user2 = two_users

        # Initial state - no relationship
        rel = rel_manager.get_relationship(user1.id, user2.id)
        assert rel.status == RelationshipStatus.NONE

        # Send request
        request = rel_manager.send_friend_request(user1.id, user2.id)
        assert request.status == FriendRequestStatus.PENDING

        # Check pending states
        rel1 = rel_manager.get_relationship(user1.id, user2.id)
        assert rel1.status == RelationshipStatus.PENDING_OUTGOING

        rel2 = rel_manager.get_relationship(user2.id, user1.id)
        assert rel2.status == RelationshipStatus.PENDING_INCOMING

        # Accept request
        rel_manager.accept_friend_request(user2.id, request.id)

        # Verify friendship
        rel1 = rel_manager.get_relationship(user1.id, user2.id)
        assert rel1.status == RelationshipStatus.FRIEND

        rel2 = rel_manager.get_relationship(user2.id, user1.id)
        assert rel2.status == RelationshipStatus.FRIEND

    def test_block_after_friendship_workflow(self, rel_manager, two_users):
        """Test blocking after being friends."""
        user1, user2 = two_users

        # Create friendship
        request = rel_manager.send_friend_request(user1.id, user2.id)
        rel_manager.accept_friend_request(user2.id, request.id)

        # Verify friendship
        rel = rel_manager.get_relationship(user1.id, user2.id)
        assert rel.status == RelationshipStatus.FRIEND

        # Block
        rel_manager.block_user(user1.id, user2.id)

        # Verify blocked state
        rel = rel_manager.get_relationship(user1.id, user2.id)
        assert rel.status == RelationshipStatus.BLOCKED

        # Verify friendship removed
        friends = rel_manager.get_friend_ids(user1.id)
        assert user2.id not in friends

    def test_unblock_and_refriend_workflow(self, rel_manager, two_users):
        """Test unblocking and becoming friends again."""
        user1, user2 = two_users

        # Block
        rel_manager.block_user(user1.id, user2.id)

        # Unblock
        rel_manager.unblock_user(user1.id, user2.id)

        # Send new friend request
        request = rel_manager.send_friend_request(user1.id, user2.id)
        rel_manager.accept_friend_request(user2.id, request.id)

        # Verify friendship
        rel = rel_manager.get_relationship(user1.id, user2.id)
        assert rel.status == RelationshipStatus.FRIEND

    def test_decline_and_resend_workflow(self, rel_manager, two_users):
        """Test declining and resending friend request."""
        user1, user2 = two_users

        # Send and decline
        request1 = rel_manager.send_friend_request(user1.id, user2.id)
        rel_manager.decline_friend_request(user2.id, request1.id)

        # Verify declined
        updated = rel_manager.get_friend_request(request1.id)
        assert updated.status == FriendRequestStatus.DECLINED

        # Send new request
        request2 = rel_manager.send_friend_request(user1.id, user2.id)
        assert request2.id != request1.id
        assert request2.status == FriendRequestStatus.PENDING

    def test_cancel_and_resend_workflow(self, rel_manager, two_users):
        """Test cancelling and resending friend request."""
        user1, user2 = two_users

        # Send and cancel
        request1 = rel_manager.send_friend_request(user1.id, user2.id)
        rel_manager.cancel_friend_request(user1.id, request1.id)

        # Send new request
        request2 = rel_manager.send_friend_request(user1.id, user2.id)
        assert request2.id != request1.id
        assert request2.status == FriendRequestStatus.PENDING


class TestConcurrentRelationships:
    """Tests for handling multiple relationships."""

    def test_multiple_pending_requests(self, db, auth_manager, rel_manager):
        """Test user with multiple pending requests."""
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

            # Create multiple users who send requests
            for i in range(5):
                sender = auth_manager.register(
                    username=f"sender_{unique_id}_{i}",
                    email=f"sender_{unique_id}_{i}@example.com",
                    password="TestPass123!",
                )
                rel_manager.send_friend_request(sender.id, main_user.id)

        # Check incoming requests
        incoming = rel_manager.get_pending_requests_incoming(main_user.id)
        assert len(incoming) == 5

    def test_multiple_friends_and_blocks(self, db, auth_manager, rel_manager):
        """Test user with multiple friends and blocks."""
        from unittest.mock import patch
        from src.utils import encryption
        import uuid

        unique_id = uuid.uuid4().hex[:6]

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            main_user = auth_manager.register(
                username=f"mainmix_{unique_id}",
                email=f"mainmix_{unique_id}@example.com",
                password="TestPass123!",
            )

            # Create friends
            for i in range(3):
                friend = auth_manager.register(
                    username=f"friendmix_{unique_id}_{i}",
                    email=f"friendmix_{unique_id}_{i}@example.com",
                    password="TestPass123!",
                )
                request = rel_manager.send_friend_request(main_user.id, friend.id)
                rel_manager.accept_friend_request(friend.id, request.id)

            # Create blocks
            for i in range(2):
                blocked = auth_manager.register(
                    username=f"blockedmix_{unique_id}_{i}",
                    email=f"blockedmix_{unique_id}_{i}@example.com",
                    password="TestPass123!",
                )
                rel_manager.block_user(main_user.id, blocked.id)

        friends = rel_manager.get_friends(main_user.id)
        blocked = rel_manager.get_blocked_users(main_user.id)

        assert len(friends) == 3
        assert len(blocked) == 2
