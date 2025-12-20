"""
Tests for message routes - sending, editing, deleting, searching messages.

Covers:
- Message CRUD operations
- Authorization checks
- Input sanitization
- SQL injection prevention
- Message search
- Pinning messages
- Read receipts
- Typing indicators
- Error handling
"""

import pytest
import asyncio
import uuid
from httpx import AsyncClient
from src.api.app import create_app


@pytest.mark.asyncio
class TestMessageCreation:
    """Test message creation endpoint."""

    async def test_send_message_to_channel(self, modules, auth_headers, test_server):
        """Test sending a message to a server channel."""
        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                f"/api/v1/channels/{channel.id}/messages",
                headers=auth_headers,
                json={"content": "Hello, world!"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["content"] == "Hello, world!"
            assert "id" in data
            assert str(data["channel_id"]) == str(channel.id)

    async def test_send_message_without_auth(self, test_server):
        """Test sending message without authentication."""
        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                f"/api/v1/channels/{channel.id}/messages",
                json={"content": "Hello, world!"},
            )

            assert response.status_code == 401

    async def test_send_empty_message(self, modules, auth_headers, test_server):
        """Test sending empty message."""
        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                f"/api/v1/channels/{channel.id}/messages",
                headers=auth_headers,
                json={"content": ""},
            )

            assert response.status_code == 400

    async def test_send_message_to_nonexistent_channel(self, auth_headers):
        """Test sending message to non-existent channel."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/channels/999999999/messages",
                headers=auth_headers,
                json={"content": "Hello"},
            )

            assert response.status_code == 404

    async def test_send_message_sql_injection(self, modules, auth_headers, test_server):
        """Test SQL injection prevention in message content."""
        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                f"/api/v1/channels/{channel.id}/messages",
                headers=auth_headers,
                json={"content": "'; DROP TABLE messages; --"},
            )

            # Should safely store the content
            assert response.status_code == 200
            data = response.json()
            assert data["content"] == "'; DROP TABLE messages; --"

    async def test_send_message_xss_content(self, modules, auth_headers, test_server):
        """Test XSS content is preserved (sanitization should be client-side)."""
        app = create_app()
        channel = test_server["channel"]
        xss_content = "<script>alert('xss')</script>"

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                f"/api/v1/channels/{channel.id}/messages",
                headers=auth_headers,
                json={"content": xss_content},
            )

            # Content should be preserved as-is
            assert response.status_code == 200
            data = response.json()
            assert xss_content in data["content"]

    async def test_send_message_with_reply(self, modules, auth_headers, test_server):
        """Test sending a reply to another message."""
        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            # Send original message
            resp1 = await ac.post(
                f"/api/v1/channels/{channel.id}/messages",
                headers=auth_headers,
                json={"content": "Original message"},
            )
            original_id = resp1.json()["id"]

            # Send reply
            resp2 = await ac.post(
                f"/api/v1/channels/{channel.id}/messages",
                headers=auth_headers,
                json={"content": "Reply message", "reply_to_id": str(original_id)},
            )

            assert resp2.status_code == 200
            data = resp2.json()
            assert data["content"] == "Reply message"
            assert str(data["reply_to_id"]) == str(original_id)

    async def test_send_message_unauthorized_channel(self, modules, session_users):
        """Test sending message to channel user doesn't have access to."""
        # Create server with one user
        user1, username1, password1 = session_users[0]
        user2, username2, password2 = session_users[1]

        server = modules.servers.create_server(
            owner_id=user1.id, name=f"Private Server {uuid.uuid4().hex[:6]}"
        )
        channels = modules.servers.get_channels(user1.id, server.id)
        channel = channels[0]

        # User2 tries to send message
        result2 = modules.auth.login(username2, password2)
        headers2 = {"Authorization": f"Bearer {result2.token}"}

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                f"/api/v1/channels/{channel.id}/messages",
                headers=headers2,
                json={"content": "Unauthorized message"},
            )

            assert response.status_code == 404  # Channel not found for this user

    async def test_send_long_message(self, modules, auth_headers, test_server):
        """Test sending message at or above length limit."""
        app = create_app()
        channel = test_server["channel"]

        # Send message at limit (should succeed)
        long_content = "a" * 4000

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                f"/api/v1/channels/{channel.id}/messages",
                headers=auth_headers,
                json={"content": long_content},
            )

            assert response.status_code == 200


@pytest.mark.asyncio
class TestMessageRetrieval:
    """Test message retrieval endpoints."""

    async def test_get_channel_messages(
        self, modules, auth_headers, test_server, test_user
    ):
        """Test getting messages from a channel."""
        app = create_app()
        channel = test_server["channel"]

        # Send some messages first
        for i in range(5):
            modules.messaging.send_message(
                user_id=test_user["user"].id,
                conversation_id=channel.conversation_id,
                content=f"Message {i}",
            )

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                f"/api/v1/channels/{channel.id}/messages", headers=auth_headers
            )

            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, list)
            assert len(data) >= 5

    async def test_get_messages_pagination(
        self, modules, auth_headers, test_server, test_user
    ):
        """Test message pagination with before/after."""
        app = create_app()
        channel = test_server["channel"]

        # Send multiple messages
        message_ids = []
        for i in range(10):
            msg = modules.messaging.send_message(
                user_id=test_user["user"].id,
                conversation_id=channel.conversation_id,
                content=f"Message {i}",
            )
            message_ids.append(msg.id)

        async with AsyncClient(app=app, base_url="http://test") as ac:
            # Get first 5 messages
            response = await ac.get(
                f"/api/v1/channels/{channel.id}/messages?limit=5", headers=auth_headers
            )

            assert response.status_code == 200
            data = response.json()
            assert len(data) <= 5

            # Get messages before a specific ID
            if len(data) > 0:
                before_id = data[0]["id"]
                response = await ac.get(
                    f"/api/v1/channels/{channel.id}/messages?before={before_id}&limit=5",
                    headers=auth_headers,
                )
                assert response.status_code == 200

    async def test_get_specific_message(
        self, modules, auth_headers, test_server, test_user
    ):
        """Test getting a specific message by ID."""
        app = create_app()
        channel = test_server["channel"]

        # Send a message
        msg = modules.messaging.send_message(
            user_id=test_user["user"].id,
            conversation_id=channel.conversation_id,
            content="Specific message",
        )

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                f"/api/v1/channels/{channel.id}/messages/{msg.id}", headers=auth_headers
            )

            assert response.status_code == 200
            data = response.json()
            assert data["content"] == "Specific message"

    async def test_get_nonexistent_message(self, auth_headers, test_server):
        """Test getting a non-existent message."""
        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                f"/api/v1/channels/{channel.id}/messages/999999999",
                headers=auth_headers,
            )

            assert response.status_code == 404

    async def test_get_messages_without_auth(self, test_server):
        """Test getting messages without authentication."""
        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(f"/api/v1/channels/{channel.id}/messages")

            assert response.status_code == 401


@pytest.mark.asyncio
class TestMessageEditing:
    """Test message editing endpoint."""

    async def test_edit_own_message(
        self, modules, auth_headers, test_server, test_user
    ):
        """Test editing own message."""
        app = create_app()
        channel = test_server["channel"]

        # Send a message
        msg = modules.messaging.send_message(
            user_id=test_user["user"].id,
            conversation_id=channel.conversation_id,
            content="Original content",
        )

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.patch(
                f"/api/v1/channels/{channel.id}/messages/{msg.id}",
                headers=auth_headers,
                json={"content": "Edited content"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["content"] == "Edited content"
            assert data["edited_at"] is not None

    async def test_edit_someone_elses_message(
        self, modules, session_users, test_server
    ):
        """Test editing someone else's message."""
        user1, username1, password1 = session_users[0]
        user2, username2, password2 = session_users[1]

        # User1 sends message
        channel = test_server["channel"]
        msg = modules.messaging.send_message(
            user_id=user1.id,
            conversation_id=channel.conversation_id,
            content="Original content",
        )

        # User2 tries to edit
        result2 = modules.auth.login(username2, password2)
        headers2 = {"Authorization": f"Bearer {result2.token}"}

        # First add user2 to server
        modules.servers.add_member(test_server["server"].id, user2.id)

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.patch(
                f"/api/v1/channels/{channel.id}/messages/{msg.id}",
                headers=headers2,
                json={"content": "Edited by user2"},
            )

            assert response.status_code == 403

    async def test_edit_message_empty_content(
        self, modules, auth_headers, test_server, test_user
    ):
        """Test editing message with empty content."""
        app = create_app()
        channel = test_server["channel"]

        # Send a message
        msg = modules.messaging.send_message(
            user_id=test_user["user"].id,
            conversation_id=channel.conversation_id,
            content="Original content",
        )

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.patch(
                f"/api/v1/channels/{channel.id}/messages/{msg.id}",
                headers=auth_headers,
                json={"content": ""},
            )

            assert response.status_code == 400

    async def test_edit_nonexistent_message(self, auth_headers, test_server):
        """Test editing a non-existent message."""
        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.patch(
                f"/api/v1/channels/{channel.id}/messages/999999999",
                headers=auth_headers,
                json={"content": "Edited"},
            )

            assert response.status_code == 404


@pytest.mark.asyncio
class TestMessageDeletion:
    """Test message deletion endpoint."""

    async def test_delete_own_message(
        self, modules, auth_headers, test_server, test_user
    ):
        """Test deleting own message."""
        app = create_app()
        channel = test_server["channel"]

        # Send a message
        msg = modules.messaging.send_message(
            user_id=test_user["user"].id,
            conversation_id=channel.conversation_id,
            content="To be deleted",
        )

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.delete(
                f"/api/v1/channels/{channel.id}/messages/{msg.id}", headers=auth_headers
            )

            assert response.status_code == 200

            # Verify message is deleted
            get_response = await ac.get(
                f"/api/v1/channels/{channel.id}/messages/{msg.id}", headers=auth_headers
            )
            assert get_response.status_code == 404

    async def test_delete_someone_elses_message_no_perms(
        self, modules, session_users, test_server
    ):
        """Test deleting someone else's message without permissions."""
        user1, username1, password1 = session_users[0]
        user2, username2, password2 = session_users[1]

        # User1 sends message
        channel = test_server["channel"]
        msg = modules.messaging.send_message(
            user_id=user1.id,
            conversation_id=channel.conversation_id,
            content="To be deleted",
        )

        # User2 tries to delete
        result2 = modules.auth.login(username2, password2)
        headers2 = {"Authorization": f"Bearer {result2.token}"}

        # Add user2 to server
        modules.servers.add_member(test_server["server"].id, user2.id)

        app = create_app()
        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.delete(
                f"/api/v1/channels/{channel.id}/messages/{msg.id}", headers=headers2
            )

            assert response.status_code == 403

    async def test_delete_nonexistent_message(self, auth_headers, test_server):
        """Test deleting a non-existent message."""
        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.delete(
                f"/api/v1/channels/{channel.id}/messages/999999999",
                headers=auth_headers,
            )

            assert response.status_code == 404


@pytest.mark.asyncio
class TestMessageSearch:
    """Test message search endpoint."""

    async def test_search_messages(self, modules, auth_headers, test_server, test_user):
        """Test searching messages in a channel."""
        app = create_app()
        channel = test_server["channel"]

        # Send some messages with keywords
        modules.messaging.send_message(
            user_id=test_user["user"].id,
            conversation_id=channel.conversation_id,
            content="Hello world this is a test",
        )
        modules.messaging.send_message(
            user_id=test_user["user"].id,
            conversation_id=channel.conversation_id,
            content="Another message about testing",
        )
        modules.messaging.send_message(
            user_id=test_user["user"].id,
            conversation_id=channel.conversation_id,
            content="Unrelated content",
        )

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                f"/api/v1/channels/{channel.id}/messages/search?content=test",
                headers=auth_headers,
            )

            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, list)
            assert len(data) >= 2

    async def test_search_messages_no_results(self, modules, auth_headers, test_server):
        """Test searching messages with no results."""
        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                f"/api/v1/channels/{channel.id}/messages/search?content=nonexistentkeyword123",
                headers=auth_headers,
            )

            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, list)
            assert len(data) == 0

    async def test_search_messages_sql_injection(
        self, modules, auth_headers, test_server
    ):
        """Test SQL injection prevention in search."""
        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                f"/api/v1/channels/{channel.id}/messages/search?content=' OR '1'='1",
                headers=auth_headers,
            )

            # Should safely handle and return results or empty
            assert response.status_code == 200


@pytest.mark.asyncio
class TestMessagePinning:
    """Test message pinning endpoints."""

    async def test_pin_message(self, modules, auth_headers, test_server, test_user):
        """Test pinning a message."""
        app = create_app()
        channel = test_server["channel"]

        # Send a message
        msg = modules.messaging.send_message(
            user_id=test_user["user"].id,
            conversation_id=channel.conversation_id,
            content="Message to pin",
        )

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.put(
                f"/api/v1/channels/{channel.id}/pins/{msg.id}", headers=auth_headers
            )

            assert response.status_code == 200

    async def test_get_pinned_messages(
        self, modules, auth_headers, test_server, test_user
    ):
        """Test getting pinned messages."""
        app = create_app()
        channel = test_server["channel"]

        # Send and pin a message
        msg = modules.messaging.send_message(
            user_id=test_user["user"].id,
            conversation_id=channel.conversation_id,
            content="Pinned message",
        )
        modules.messaging.pin_message(test_user["user"].id, msg.id)

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                f"/api/v1/channels/{channel.id}/pins", headers=auth_headers
            )

            assert response.status_code == 200
            data = response.json()
            assert isinstance(data, list)

    async def test_unpin_message(self, modules, auth_headers, test_server, test_user):
        """Test unpinning a message."""
        app = create_app()
        channel = test_server["channel"]

        # Send and pin a message
        msg = modules.messaging.send_message(
            user_id=test_user["user"].id,
            conversation_id=channel.conversation_id,
            content="Pinned message",
        )
        modules.messaging.pin_message(test_user["user"].id, msg.id)

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.delete(
                f"/api/v1/channels/{channel.id}/pins/{msg.id}", headers=auth_headers
            )

            assert response.status_code == 200


@pytest.mark.asyncio
class TestReadReceipts:
    """Test read receipt endpoints."""

    async def test_acknowledge_messages(
        self, modules, auth_headers, test_server, test_user
    ):
        """Test marking messages as read."""
        app = create_app()
        channel = test_server["channel"]

        # Send some messages
        msg = modules.messaging.send_message(
            user_id=test_user["user"].id,
            conversation_id=channel.conversation_id,
            content="Test message",
        )

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                f"/api/v1/channels/{channel.id}/messages/ack?message_id={msg.id}",
                headers=auth_headers,
            )

            assert response.status_code == 200
            data = response.json()
            assert "success" in data

    async def test_get_unread_count(self, modules, auth_headers, test_server):
        """Test getting unread message count."""
        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get(
                f"/api/v1/channels/{channel.id}/messages/unread", headers=auth_headers
            )

            assert response.status_code == 200
            data = response.json()
            assert "unread_count" in data

    async def test_get_all_unread_counts(self, auth_headers):
        """Test getting unread counts for all channels."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.get("/api/v1/users/@me/unread", headers=auth_headers)

            assert response.status_code == 200
            data = response.json()
            assert "unread_counts" in data


@pytest.mark.asyncio
class TestTypingIndicator:
    """Test typing indicator endpoint."""

    async def test_trigger_typing(self, modules, auth_headers, test_server):
        """Test triggering typing indicator."""
        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                f"/api/v1/channels/{channel.id}/typing", headers=auth_headers
            )

            assert response.status_code == 200

    async def test_typing_without_auth(self, test_server):
        """Test typing indicator without authentication."""
        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(f"/api/v1/channels/{channel.id}/typing")

            assert response.status_code == 401

    async def test_typing_nonexistent_channel(self, auth_headers):
        """Test typing indicator for non-existent channel."""
        app = create_app()

        async with AsyncClient(app=app, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/channels/999999999/typing", headers=auth_headers
            )

            # Should return 404 or 200 (implementation dependent)
            assert response.status_code in [200, 404]


@pytest.mark.asyncio
class TestConcurrentOperations:
    """Test concurrent message operations."""

    async def test_concurrent_message_sends(self, modules, auth_headers, test_server):
        """Test sending multiple messages concurrently."""
        app = create_app()
        channel = test_server["channel"]

        async with AsyncClient(app=app, base_url="http://test") as ac:
            tasks = [
                ac.post(
                    f"/api/v1/channels/{channel.id}/messages",
                    headers=auth_headers,
                    json={"content": f"Concurrent message {i}"},
                )
                for i in range(10)
            ]
            responses = await asyncio.gather(*tasks)

            # All should succeed
            for resp in responses:
                assert resp.status_code == 200

    async def test_concurrent_message_edits(
        self, modules, auth_headers, test_server, test_user
    ):
        """Test editing same message concurrently."""
        app = create_app()
        channel = test_server["channel"]

        # Send a message
        msg = modules.messaging.send_message(
            user_id=test_user["user"].id,
            conversation_id=channel.conversation_id,
            content="Original",
        )

        async with AsyncClient(app=app, base_url="http://test") as ac:
            tasks = [
                ac.patch(
                    f"/api/v1/channels/{channel.id}/messages/{msg.id}",
                    headers=auth_headers,
                    json={"content": f"Edit {i}"},
                )
                for i in range(5)
            ]
            responses = await asyncio.gather(*tasks)

            # All should succeed (last one wins)
            success_count = sum(1 for r in responses if r.status_code == 200)
            assert success_count >= 1
