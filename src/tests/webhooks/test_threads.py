"""
Tests for webhook posting to threads.
"""


class TestWebhookThreads:
    """Tests for posting webhook messages to threads."""

    def test_execute_to_thread(self, webhook_with_token):
        """Test posting webhook message to a thread."""
        setup = webhook_with_token
        thread_id = 123456789

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="Thread message",
            thread_id=thread_id,
            wait=True,
        )

        assert result is not None
        assert result.thread_id == thread_id

    def test_execute_to_thread_with_embeds(self, webhook_with_token):
        """Test posting embed to thread."""
        setup = webhook_with_token
        thread_id = 123456789

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            embeds=[{"title": "Thread Embed"}],
            thread_id=thread_id,
            wait=True,
        )

        assert result.thread_id == thread_id
        assert len(result.embeds) == 1

    def test_execute_to_thread_with_overrides(self, webhook_with_token):
        """Test posting to thread with username/avatar overrides."""
        setup = webhook_with_token
        thread_id = 123456789

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="Thread with overrides",
            username="Thread Bot",
            avatar_url="https://example.com/thread.png",
            thread_id=thread_id,
            wait=True,
        )

        assert result.thread_id == thread_id
        assert result.username == "Thread Bot"
        assert result.avatar_url == "https://example.com/thread.png"

    def test_execute_no_thread_id(self, webhook_with_token):
        """Test that thread_id is None when not specified."""
        setup = webhook_with_token

        result = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="No thread",
            wait=True,
        )

        assert result.thread_id is None

    def test_execute_to_thread_via_url(self, webhook_with_token):
        """Test posting to thread via URL execution."""
        setup = webhook_with_token
        thread_id = 987654321

        result = setup["webhooks"].execute_webhook_by_url(
            webhook_url=setup["webhook"].url,
            content="URL thread message",
            thread_id=thread_id,
            wait=True,
        )

        assert result.thread_id == thread_id

    def test_execute_multiple_to_same_thread(self, webhook_with_token):
        """Test posting multiple messages to same thread."""
        setup = webhook_with_token
        thread_id = 111222333

        results = []
        for i in range(3):
            result = setup["webhooks"].execute_webhook(
                webhook_id=setup["webhook"].id,
                token=setup["token"],
                content=f"Thread message {i}",
                thread_id=thread_id,
                wait=True,
            )
            results.append(result)

        for result in results:
            assert result.thread_id == thread_id

        message_ids = [r.id for r in results]
        assert len(set(message_ids)) == 3

    def test_execute_to_different_threads(self, webhook_with_token):
        """Test posting to different threads."""
        setup = webhook_with_token

        result1 = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="Thread 1 message",
            thread_id=111,
            wait=True,
        )

        result2 = setup["webhooks"].execute_webhook(
            webhook_id=setup["webhook"].id,
            token=setup["token"],
            content="Thread 2 message",
            thread_id=222,
            wait=True,
        )

        assert result1.thread_id == 111
        assert result2.thread_id == 222
