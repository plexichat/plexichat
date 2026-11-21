"""Public-thread membership changes must be gated on the caller's channel view.

``can_manage_thread`` is gated on parent-channel visibility, but ``add_member``
only consults it for PRIVATE threads. A member of a PUBLIC thread whose
``channels.view`` has since been revoked (e.g. a new channel override) can
still add arbitrary server members to the now-hidden thread: they remain a
``thread_members`` row after losing view, so the bare membership check passes
and only the target's view is validated. That lets a caller mutate the roster
of a thread they can no longer see and hand access to a third party.

The view denial is applied to the CALLER only, so the operation is otherwise
valid -- proving the gap is the missing caller-side view gate, not an
unrelated rejection. Uses raw rows and a stub resolver so the test stays
independent of the global RBAC cache and the API app import graph.
"""

import pytest

from src.core.threads import ThreadAccessDeniedError
from src.core.threads.manager import ThreadManager


class _SelectiveViewServers:
    """Permission resolver that can revoke ``channels.view`` for one user."""

    def __init__(self):
        self.deny_view_for = None

    def has_permission(self, user_id, server_id, permission, channel_id=None):
        if permission == "channels.view" and user_id == self.deny_view_for:
            return False
        return True


@pytest.fixture
def manager_and_rows(db):
    db.execute(
        "INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) "
        "VALUES (1, 'S', 1, 1000, 1000)"
    )
    db.execute(
        "INSERT INTO srv_channels "
        "(id, server_id, name, channel_type, created_at, updated_at, position) "
        "VALUES (1, 1, 'general', 'text', 1000, 1000, 0)"
    )
    db.execute(
        "INSERT INTO srv_members (id, server_id, user_id, joined_at, updated_at) "
        "VALUES (1, 1, 1, 1000, 1000), (2, 1, 2, 1000, 1000), (3, 1, 3, 1000, 1000)"
    )
    servers = _SelectiveViewServers()
    threads = ThreadManager(db, servers_module=servers)
    # User 1 creates a PUBLIC thread while they can still view the channel,
    # so they are its owner/member with a live view grant at first.
    thread = threads.create_thread(1, 1, "public")
    return threads, servers, thread.id


def test_public_thread_add_member_requires_caller_channel_view(manager_and_rows):
    threads, servers, tid = manager_and_rows

    assert threads._is_member(1, tid) is True
    assert threads.can_view_thread(1, tid) is True
    assert threads._is_member(2, tid) is False

    # Channel-specific override revokes only the caller's view.
    servers.deny_view_for = 1
    assert threads.can_view_thread(1, tid) is False
    # The target can still view, so the add is otherwise fully valid.
    assert threads.can_view_thread(2, tid) is True

    with pytest.raises(ThreadAccessDeniedError):
        threads.add_member(1, tid, 2)

    assert threads._is_member(2, tid) is False

    # Positive control: restore the caller's view and the same operation
    # succeeds, proving the denial above is caused by the view gate alone.
    servers.deny_view_for = None
    assert threads.can_view_thread(1, tid) is True
    threads.add_member(1, tid, 2)
    assert threads._is_member(2, tid) is True
