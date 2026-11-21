"""Thread state operations must not work on a thread the caller cannot view.

Ownership (and a server-scoped ``threads.manage`` grant) survives a later
channel override that revokes ``channels.view``.  Archive / unarchive / lock /
unlock / rename / delete all funnel through ``can_manage_thread``; that check
used to grant the owner (and a manage-grant holder) without ever confirming the
caller can still see the parent channel, so a hidden thread stayed fully
operable.  These tests pin the fail-closed channel-view gate at that choke
point and verify no state mutation happens on denial.

A stub servers module is used for the "revoked" side so the test does not
depend on the global RBAC permission cache's TTL/epoch behaviour.
"""

import uuid

import pytest

from src.core.threads.exceptions import PermissionDeniedError
from src.core.threads.manager import ThreadManager


class _NoChannelView:
    """Permission resolver on the real server with ``channels.view`` revoked."""

    def __init__(self, real):
        self._real = real

    def has_permission(self, user_id, server_id, permission, channel_id=None):
        if permission == "channels.view":
            return False
        return self._real.has_permission(user_id, server_id, permission, channel_id)


@pytest.fixture
def thread_and_owner(modules):
    suffix = uuid.uuid4().hex[:10]
    owner = modules.auth.register(
        username=f"gate_owner_{suffix}",
        email=f"gate_owner_{suffix}@example.com",
        password="TestPass123!",
    )
    member = modules.auth.register(
        username=f"gate_member_{suffix}",
        email=f"gate_member_{suffix}@example.com",
        password="TestPass123!",
    )
    server = modules.servers.create_server(owner.id, f"Gate {suffix}")
    modules.servers.add_member(server.id, member.id)
    role = modules.servers.create_role(
        owner.id,
        server.id,
        "Members",
        permissions={
            "channels.view": True,
            "messages.send": True,
            "threads.create_public": True,
        },
    )
    modules.servers.assign_role(owner.id, server.id, member.id, role.id)
    channel = modules.servers.create_channel(
        owner.id, server.id, "general", channel_type=modules.servers.ChannelType.TEXT
    )
    threads = ThreadManager(
        modules.threads._db,
        auth_module=modules.auth,
        messaging_module=modules.messaging,
        servers_module=modules.servers,
    )
    # The member creates and therefore owns the thread while view is granted.
    thread = threads.create_thread(member.id, channel.id, "owned")
    return member, thread, threads, modules


def _row(db, thread_id):
    return db.fetch_one("SELECT * FROM thread_threads WHERE id = ?", (thread_id,))


def test_owned_hidden_thread_operations_denied_without_mutation(thread_and_owner):
    member, thread, threads, modules = thread_and_owner
    tid = thread.id

    real_servers = threads._servers
    assert threads.can_manage_thread(member.id, tid) is True

    # Revoke the channel view after ownership was established. No cache is
    # involved: this is the authoritative answer for every permission check.
    threads._servers = _NoChannelView(real_servers)
    assert threads.can_view_thread(member.id, tid) is False
    assert threads.get_thread(member.id, tid) is None
    assert threads.can_manage_thread(member.id, tid) is False

    baseline = dict(_row(modules.threads._db, tid))

    with pytest.raises(PermissionDeniedError):
        threads.archive_thread(member.id, tid)
    with pytest.raises(PermissionDeniedError):
        threads.unarchive_thread(member.id, tid)
    with pytest.raises(PermissionDeniedError):
        threads.lock_thread(member.id, tid)
    with pytest.raises(PermissionDeniedError):
        threads.unlock_thread(member.id, tid)
    with pytest.raises(PermissionDeniedError):
        threads.update_thread(member.id, tid, name="renamed")
    with pytest.raises(PermissionDeniedError):
        threads.delete_thread(member.id, tid)

    after = dict(_row(modules.threads._db, tid))
    for column in ("state", "locked", "name", "deleted"):
        assert after[column] == baseline[column], f"{column} mutated while hidden"


def test_hidden_thread_operations_resume_after_view_restored(thread_and_owner):
    """Positive control: the denial above is caused by the view gate only."""
    member, thread, threads, modules = thread_and_owner
    tid = thread.id

    real_servers = threads._servers
    threads._servers = _NoChannelView(real_servers)
    with pytest.raises(PermissionDeniedError):
        threads.archive_thread(member.id, tid)

    threads._servers = real_servers
    assert threads.can_manage_thread(member.id, tid) is True

    assert threads.archive_thread(member.id, tid).state == threads.ThreadState.ARCHIVED
    assert threads.lock_thread(member.id, tid).locked is True
    assert threads.update_thread(member.id, tid, name="renamed").name == "renamed"
