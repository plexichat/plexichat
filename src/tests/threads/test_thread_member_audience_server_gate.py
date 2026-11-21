"""Private-thread gateway audience must not outlive backing-server membership.

Leaving/kicking a server removes the ``srv_members`` row but does not prune
``thread_members``. Gateway emitters scope private-thread events to the exact
member ids returned by ``get_all_member_user_ids``; if that helper trusts the
stale ``thread_members`` row, a former server member keeps receiving private
thread events (thread name/owner/existence, message and typing activity)
after they no longer belong to the server.
"""

import pytest

from src.core.threads import ThreadType


@pytest.mark.unit
def test_former_server_member_excluded_from_private_thread_audience(
    server_with_channel,
):
    owner, member1, _member2, server, channel, servers, threads = server_with_channel

    thread = threads.create_thread(
        user_id=owner.id,
        channel_id=channel.id,
        name="Secret Planning",
        thread_type=ThreadType.PRIVATE,
    )
    threads.add_member(owner.id, thread.id, member1.id)

    # While still a server member, membership grants the private-thread audience.
    assert member1.id in threads.get_all_member_user_ids(thread.id)

    # The member leaves the backing server; the thread_members row survives.
    assert servers.remove_member(member1.id, server.id) is True
    assert servers.get_member(server.id, member1.id) is None
    assert threads._is_member(member1.id, thread.id) is True  # stale row

    # Fail closed: the stale row must not keep them in the event audience.
    audience = threads.get_all_member_user_ids(thread.id)
    assert member1.id not in audience
    assert owner.id in audience
