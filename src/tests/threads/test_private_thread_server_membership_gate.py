"""Private-thread access must not outlive backing-server membership.

Leaving/kicking a server removes the ``srv_members`` row but does not prune
``thread_members``. The private-thread visibility gate must therefore
re-check the server roster instead of trusting the stale membership row,
otherwise a former member keeps reading the thread's name/messages and
stays in its gateway-event audience (a cross-server participant leak).
"""

import pytest

from src.core.threads import ThreadType, ThreadAccessDeniedError


def test_removed_server_member_loses_private_thread_access(server_with_channel):
    owner, member1, _member2, server, channel, servers, threads = server_with_channel

    thread = threads.create_thread(
        user_id=owner.id,
        channel_id=channel.id,
        name="Secret Planning",
        thread_type=ThreadType.PRIVATE,
    )
    threads.add_member(owner.id, thread.id, member1.id)

    # While still a server member, membership grants private-thread access.
    assert threads.can_view_thread(member1.id, thread.id) is True
    assert threads.get_thread(member1.id, thread.id) is not None

    # The member leaves the backing server; the thread_members row survives.
    assert servers.remove_member(member1.id, server.id) is True
    assert servers.get_member(server.id, member1.id) is None
    assert threads._is_member(member1.id, thread.id) is True  # stale row

    # Fail closed: the stale row must no longer grant thread access.
    assert threads.can_view_thread(member1.id, thread.id) is False
    assert threads.get_thread(member1.id, thread.id) is None
    with pytest.raises(ThreadAccessDeniedError):
        threads.get_messages(member1.id, thread.id)


def test_removed_server_member_loses_public_thread_access(server_with_channel):
    """Sanity: public-thread access was already server-gated; keep it that way."""
    owner, member1, _member2, server, channel, servers, threads = server_with_channel

    thread = threads.create_thread(
        user_id=owner.id, channel_id=channel.id, name="Public"
    )
    assert threads.can_view_thread(member1.id, thread.id) is True

    assert servers.remove_member(member1.id, server.id) is True
    assert threads.can_view_thread(member1.id, thread.id) is False
