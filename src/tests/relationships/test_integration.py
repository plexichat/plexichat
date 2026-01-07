"""
Tests for integration with other modules (auth, servers, messaging).
"""

from src.core.relationships import (
    RelationshipStatus,
    FriendRequestStatus,
)


class TestAuthIntegration:
    """Tests for integration with auth module."""

    def test_relationship_with_valid_users(self, fresh_users):
        """Test that relationships work with valid auth users."""
        user1, user2, relationships = fresh_users

        request = relationships.send_friend_request(user1.id, user2.id)

        assert request.sender_id == user1.id
        assert request.recipient_id == user2.id

    def test_get_relationship_with_auth_users(self, fresh_users):
        """Test getting relationship status with auth users."""
        user1, user2, relationships = fresh_users

        rel = relationships.get_relationship(user1.id, user2.id)

        assert rel.user_id == user1.id
        assert rel.target_user_id == user2.id
        assert rel.status == RelationshipStatus.NONE


class TestServersIntegration:
    """Tests for integration with servers module."""

    def test_mutual_servers_with_server_members(self, users_with_server):
        """Test mutual servers calculation with actual server members."""
        user1, user2, user3, server, servers, relationships = users_with_server

        mutual = relationships.get_mutual_servers(user1.id, user2.id)

        assert server.id in mutual

    def test_mutual_servers_excludes_non_members(self, users_with_server):
        """Test that non-members are not included in mutual servers."""
        user1, user2, user3, server, servers, relationships = users_with_server

        # User3 is not in the server
        mutual = relationships.get_mutual_servers(user1.id, user3.id)

        assert server.id not in mutual

    def test_mutual_servers_multiple_servers(self, db_and_modules):
        """Test mutual servers with multiple shared servers."""
        db, auth, servers, relationships = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:6]

        user1 = auth.register(
            username=f"multi1_{unique_id}",
            email=f"multi1_{unique_id}@example.com",
            password="TestPass123!",
        )
        user2 = auth.register(
            username=f"multi2_{unique_id}",
            email=f"multi2_{unique_id}@example.com",
            password="TestPass123!",
        )

        # Create multiple servers
        server1 = servers.create_server(user1.id, f"Server1 {unique_id}")
        server2 = servers.create_server(user1.id, f"Server2 {unique_id}")

        # Add user2 to both
        servers.add_member(server1.id, user2.id)
        servers.add_member(server2.id, user2.id)

        mutual = relationships.get_mutual_servers(user1.id, user2.id)

        assert server1.id in mutual
        assert server2.id in mutual
        assert len(mutual) >= 2


class TestRelationshipWorkflows:
    """Tests for complete relationship workflows."""

    def test_full_friend_request_workflow(self, fresh_users):
        """Test complete friend request workflow."""
        user1, user2, relationships = fresh_users

        # Initial state - no relationship
        rel = relationships.get_relationship(user1.id, user2.id)
        assert rel.status == RelationshipStatus.NONE

        # Send request
        request = relationships.send_friend_request(user1.id, user2.id)
        assert request.status == FriendRequestStatus.PENDING

        # Check pending states
        rel1 = relationships.get_relationship(user1.id, user2.id)
        assert rel1.status == RelationshipStatus.PENDING_OUTGOING

        rel2 = relationships.get_relationship(user2.id, user1.id)
        assert rel2.status == RelationshipStatus.PENDING_INCOMING

        # Accept request
        relationships.accept_friend_request(user2.id, request.id)

        # Verify friendship
        rel1 = relationships.get_relationship(user1.id, user2.id)
        assert rel1.status == RelationshipStatus.FRIEND

        rel2 = relationships.get_relationship(user2.id, user1.id)
        assert rel2.status == RelationshipStatus.FRIEND

    def test_block_after_friendship_workflow(self, friends_pair):
        """Test blocking after being friends."""
        user1, user2, relationships = friends_pair

        # Verify friendship
        rel = relationships.get_relationship(user1.id, user2.id)
        assert rel.status == RelationshipStatus.FRIEND

        # Block
        relationships.block_user(user1.id, user2.id)

        # Verify blocked state
        rel = relationships.get_relationship(user1.id, user2.id)
        assert rel.status == RelationshipStatus.BLOCKED

        # Verify friendship removed
        friends = relationships.get_friend_ids(user1.id)
        assert user2.id not in friends

    def test_unblock_and_refriend_workflow(self, fresh_users):
        """Test unblocking and becoming friends again."""
        user1, user2, relationships = fresh_users

        # Block
        relationships.block_user(user1.id, user2.id)

        # Unblock
        relationships.unblock_user(user1.id, user2.id)

        # Send new friend request
        request = relationships.send_friend_request(user1.id, user2.id)
        relationships.accept_friend_request(user2.id, request.id)

        # Verify friendship
        rel = relationships.get_relationship(user1.id, user2.id)
        assert rel.status == RelationshipStatus.FRIEND

    def test_decline_and_resend_workflow(self, fresh_users):
        """Test declining and resending friend request."""
        user1, user2, relationships = fresh_users

        # Send and decline
        request1 = relationships.send_friend_request(user1.id, user2.id)
        relationships.decline_friend_request(user2.id, request1.id)

        # Verify declined
        updated = relationships.get_friend_request(request1.id)
        assert updated.status == FriendRequestStatus.DECLINED

        # Send new request
        request2 = relationships.send_friend_request(user1.id, user2.id)
        assert request2.id != request1.id
        assert request2.status == FriendRequestStatus.PENDING

    def test_cancel_and_resend_workflow(self, fresh_users):
        """Test cancelling and resending friend request."""
        user1, user2, relationships = fresh_users

        # Send and cancel
        request1 = relationships.send_friend_request(user1.id, user2.id)
        relationships.cancel_friend_request(user1.id, request1.id)

        # Send new request
        request2 = relationships.send_friend_request(user1.id, user2.id)
        assert request2.id != request1.id
        assert request2.status == FriendRequestStatus.PENDING


class TestConcurrentRelationships:
    """Tests for handling multiple relationships."""

    def test_multiple_pending_requests(self, db_and_modules):
        """Test user with multiple pending requests."""
        db, auth, servers, relationships = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:6]

        # Create main user
        main_user = auth.register(
            username=f"main_{unique_id}",
            email=f"main_{unique_id}@example.com",
            password="TestPass123!",
        )

        # Create multiple users who send requests
        for i in range(5):
            sender = auth.register(
                username=f"sender_{unique_id}_{i}",
                email=f"sender_{unique_id}_{i}@example.com",
                password="TestPass123!",
            )
            relationships.send_friend_request(sender.id, main_user.id)

        # Check incoming requests
        incoming = relationships.get_pending_requests_incoming(main_user.id)
        assert len(incoming) == 5

    def test_multiple_friends_and_blocks(self, db_and_modules):
        """Test user with multiple friends and blocks."""
        db, auth, servers, relationships = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:6]

        main_user = auth.register(
            username=f"mainmix_{unique_id}",
            email=f"mainmix_{unique_id}@example.com",
            password="TestPass123!",
        )

        # Create friends
        for i in range(3):
            friend = auth.register(
                username=f"friendmix_{unique_id}_{i}",
                email=f"friendmix_{unique_id}_{i}@example.com",
                password="TestPass123!",
            )
            request = relationships.send_friend_request(main_user.id, friend.id)
            relationships.accept_friend_request(friend.id, request.id)

        # Create blocks
        for i in range(2):
            blocked = auth.register(
                username=f"blockedmix_{unique_id}_{i}",
                email=f"blockedmix_{unique_id}_{i}@example.com",
                password="TestPass123!",
            )
            relationships.block_user(main_user.id, blocked.id)

        friends = relationships.get_friends(main_user.id)
        blocked = relationships.get_blocked_users(main_user.id)

        assert len(friends) == 3
        assert len(blocked) == 2
