"""Regression tests: thread message listing ``limit`` must be clamped.

``ThreadManager.get_messages`` passed ``limit`` straight into the SQL
``LIMIT ?`` with no lower bound. SQLite treats ``LIMIT -1`` as *unlimited*,
so ``GET /threads/{id}/messages?limit=-1`` (which accepts any ``int``)
dumped the caller's whole visible thread history instead of an empty page.
"""


def _thread_with_messages(server_with_channel, count=3):
    owner, member1, member2, server, channel, servers, threads = server_with_channel
    thread = threads.create_thread(
        user_id=owner.id, channel_id=channel.id, name="Limit Clamp Test"
    )
    for i in range(count):
        threads.send_message(owner.id, thread.id, f"message {i}")
    assert len(threads.get_messages(owner.id, thread.id)) == count
    return threads, owner, thread


class TestNegativeLimitClamp:
    def test_negative_limit_returns_empty(self, server_with_channel):
        """A negative limit must not bypass into an unlimited SELECT."""
        threads, owner, thread = _thread_with_messages(server_with_channel)
        assert threads.get_messages(owner.id, thread.id, limit=-1) == []

    def test_zero_limit_returns_empty(self, server_with_channel):
        threads, owner, thread = _thread_with_messages(server_with_channel)
        assert threads.get_messages(owner.id, thread.id, limit=0) == []

    def test_positive_limit_still_honoured(self, server_with_channel):
        threads, owner, thread = _thread_with_messages(server_with_channel)
        assert len(threads.get_messages(owner.id, thread.id, limit=50)) == 3
        assert len(threads.get_messages(owner.id, thread.id, limit=2)) == 2
