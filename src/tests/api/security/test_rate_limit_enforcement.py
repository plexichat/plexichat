"""
Test rate limit enforcement across API endpoints.

Tests that rate limits are properly enforced for:
- Authentication endpoints (login, register)
- Message sending
- Friend requests
- Server creation
- API calls in general
"""

import pytest
import time


@pytest.mark.slow
class TestAuthenticationRateLimits:
    """Test rate limits on authentication endpoints."""

    def test_login_rate_limit_enforced(self, rate_limit_client):
        """Test login endpoint enforces rate limits."""
        responses = []
        for i in range(10):
            response = rate_limit_client.post(
                "/api/v1/auth/login",
                json={"username": f"nonexistent_{i}", "password": "TestPass123!"},
            )
            responses.append(response.status_code)

        assert 429 in responses, (
            "Rate limit should be triggered on excessive login attempts"
        )

    def test_register_rate_limit_enforced(self, rate_limit_client):
        """Test register endpoint enforces rate limits."""
        responses = []
        for i in range(6):
            response = rate_limit_client.post(
                "/api/v1/auth/register",
                json={
                    "username": f"rateLimitTest_{i}_{time.time()}",
                    "email": f"ratelimit{i}_{time.time()}@test.com",
                    "password": "TestPass123!",
                },
            )
            responses.append(response.status_code)

        assert 429 in responses or responses.count(409) > 3, (
            "Rate limit should be enforced"
        )

    def test_2fa_attempt_rate_limit_enforced(self, rate_limit_client):
        """Test 2FA completion endpoint enforces rate limits."""
        responses = []
        for i in range(10):
            response = rate_limit_client.post(
                "/api/v1/auth/2fa",
                json={"challenge_token": f"fake_token_{i}", "code": "123456"},
            )
            responses.append(response.status_code)

        assert 429 in responses, "Rate limit should be enforced on 2FA attempts"


@pytest.mark.slow
class TestMessagingRateLimits:
    """Test rate limits on messaging endpoints."""

    def test_message_send_rate_limit_enforced(
        self, rate_limit_client, modules, create_user_with_token
    ):
        """Test message sending enforces rate limits."""
        user1 = create_user_with_token()
        user2 = create_user_with_token()

        dm = modules.messaging.create_dm(user1["user"].id, user2["user"].id)

        responses = []
        for i in range(20):
            response = rate_limit_client.post(
                f"/api/v1/channels/{dm.id}/messages",
                headers={"Authorization": f"Bearer {user1['token']}"},
                json={"content": f"Message {i}"},
            )
            responses.append(response.status_code)

        assert 429 in responses, "Rate limit should be enforced on message spam"

    def test_message_edit_rate_limit_enforced(
        self, rate_limit_client, modules, create_user_with_token
    ):
        """Test message editing enforces rate limits."""
        user1 = create_user_with_token()
        user2 = create_user_with_token()

        dm = modules.messaging.create_dm(user1["user"].id, user2["user"].id)
        msg = modules.messaging.send_message(
            user_id=user1["user"].id, conversation_id=dm.id, content="Original message"
        )

        responses = []
        for i in range(15):
            response = rate_limit_client.patch(
                f"/api/v1/channels/{dm.id}/messages/{msg.id}",
                headers={"Authorization": f"Bearer {user1['token']}"},
                json={"content": f"Edited {i}"},
            )
            responses.append(response.status_code)

        assert 429 in responses, "Rate limit should be enforced on rapid message edits"

    def test_message_delete_rate_limit_enforced(
        self, rate_limit_client, modules, create_user_with_token
    ):
        """Test message deletion enforces rate limits."""
        user1 = create_user_with_token()
        user2 = create_user_with_token()

        dm = modules.messaging.create_dm(user1["user"].id, user2["user"].id)

        message_ids = []
        for i in range(15):
            msg = modules.messaging.send_message(
                user_id=user1["user"].id, conversation_id=dm.id, content=f"Message {i}"
            )
            message_ids.append(msg.id)

        responses = []
        for msg_id in message_ids:
            response = rate_limit_client.delete(
                f"/api/v1/channels/{dm.id}/messages/{msg_id}",
                headers={"Authorization": f"Bearer {user1['token']}"},
            )
            responses.append(response.status_code)

        assert 429 in responses, (
            "Rate limit should be enforced on rapid message deletions"
        )


@pytest.mark.slow
class TestRelationshipRateLimits:
    """Test rate limits on relationship endpoints."""

    def test_friend_request_rate_limit_enforced(
        self, rate_limit_client, create_user_with_token
    ):
        """Test friend request sending enforces rate limits."""
        user = create_user_with_token()

        target_users = [create_user_with_token() for _ in range(10)]

        responses = []
        for target in target_users:
            response = rate_limit_client.post(
                "/api/v1/relationships",
                headers={"Authorization": f"Bearer {user['token']}"},
                json={"user_id": str(target["user"].id)},
            )
            responses.append(response.status_code)

        assert 429 in responses, "Rate limit should be enforced on friend request spam"

    def test_block_rate_limit_enforced(self, rate_limit_client, create_user_with_token):
        """Test blocking users enforces rate limits."""
        user = create_user_with_token()

        target_users = [create_user_with_token() for _ in range(20)]

        responses = []
        for target in target_users:
            response = rate_limit_client.post(
                "/api/v1/relationships/block",
                headers={"Authorization": f"Bearer {user['token']}"},
                json={"user_id": str(target["user"].id)},
            )
            responses.append(response.status_code)

        assert 429 in responses, "Rate limit should be enforced on block spam"


@pytest.mark.slow
class TestServerRateLimits:
    """Test rate limits on server operations."""

    def test_server_creation_rate_limit_enforced(
        self, rate_limit_client, create_user_with_token
    ):
        """Test server creation enforces rate limits."""
        user = create_user_with_token()

        responses = []
        for i in range(15):
            response = rate_limit_client.post(
                "/api/v1/servers",
                headers={"Authorization": f"Bearer {user['token']}"},
                json={"name": f"Server {i}"},
            )
            responses.append(response.status_code)

        assert 429 in responses, "Rate limit should be enforced on server creation spam"


@pytest.mark.slow
class TestReactionRateLimits:
    """Test rate limits on reaction operations."""

    def test_reaction_add_rate_limit_enforced(
        self, rate_limit_client, modules, create_user_with_token
    ):
        """Test adding reactions enforces rate limits."""
        user1 = create_user_with_token()
        user2 = create_user_with_token()

        dm = modules.messaging.create_dm(user1["user"].id, user2["user"].id)

        messages = []
        for i in range(10):
            msg = modules.messaging.send_message(
                user_id=user1["user"].id, conversation_id=dm.id, content=f"Message {i}"
            )
            messages.append(msg)

        responses = []
        for msg in messages:
            response = rate_limit_client.put(
                f"/api/v1/channels/{dm.id}/messages/{msg.id}/reactions/👍",
                headers={"Authorization": f"Bearer {user2['token']}"},
            )
            responses.append(response.status_code)

        assert 429 in responses, "Rate limit should be enforced on rapid reactions"


@pytest.mark.slow
class TestProfileUpdateRateLimits:
    """Test rate limits on profile updates."""

    def test_profile_update_rate_limit_enforced(
        self, rate_limit_client, create_user_with_token
    ):
        """Test profile updates enforce rate limits."""
        user = create_user_with_token()

        responses = []
        for i in range(5):
            response = rate_limit_client.patch(
                "/api/v1/users/@me",
                headers={"Authorization": f"Bearer {user['token']}"},
                json={"username": f"newname{i}_{time.time()}"},
            )
            responses.append(response.status_code)

        assert 429 in responses or responses.count(409) > 2, (
            "Rate limit should be enforced"
        )


@pytest.mark.slow
class TestRateLimitHeaders:
    """Test that rate limit headers are properly set."""

    def test_rate_limit_headers_present(
        self, rate_limit_client, create_user_with_token
    ):
        """Test that rate limit headers are included in responses."""
        user = create_user_with_token()

        response = rate_limit_client.get(
            "/api/v1/users/@me", headers={"Authorization": f"Bearer {user['token']}"}
        )

        if response.status_code == 200:
            assert any(
                h.lower().startswith("x-ratelimit") for h in response.headers.keys()
            ), "Rate limit headers should be present"

    def test_rate_limit_reset_header(self, rate_limit_client, create_user_with_token):
        """Test that rate limit reset time is provided."""
        for i in range(10):
            response = rate_limit_client.post(
                "/api/v1/auth/login",
                json={"username": f"test_{i}", "password": "wrong"},
            )
            if response.status_code == 429:
                assert any(
                    h.lower() in ["x-ratelimit-reset", "retry-after"]
                    for h in response.headers.keys()
                ), "Rate limit reset header should be present on 429"
                break
