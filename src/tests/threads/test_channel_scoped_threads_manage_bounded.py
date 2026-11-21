"""Security verification: a channel-only ``threads.manage`` grant is bounded.

``threads.manage`` is catalogued as a server-scoped permission, but
``can_manage_thread`` resolves it WITH the thread's backing ``channel_id``
(the mismatch allowlisted in 3f880ef3 / N549). This is NOT the 72d3f648
escalation: every gate that consumes ``can_manage_thread`` operates on a single
thread in that thread's own channel, so a per-channel ``threads.manage`` grant
can only manage threads in the channel it was granted in. There is no
server-level destructive write reachable through it -- the only bulk operation,
``delete_threads_for_channel``, is invoked exclusively from channel deletion,
which is itself gated by the server-scoped ``channels.manage``.

These tests prove the property against the real RBAC resolver (a genuine
per-channel member override), not a stub:

* the grant applies in the granted channel (bounded positive control);
* the same grant is absent at server scope and in a second channel;
* a cross-channel thread operation is DENIED and does not mutate the thread;
* the bounded grant still works inside its channel (no over-tightening).
"""

import uuid

import pytest

from src.core.threads.exceptions import PermissionDeniedError


@pytest.fixture
def two_channel_scenario(modules, user_pool):
    servers = modules.servers
    unique = uuid.uuid4().hex[:8]
    owner = user_pool.get_user()
    grantee = user_pool.get_user()

    server = servers.create_server(owner_id=owner.id, name=f"Bounded {unique}")
    servers.add_member(server.id, grantee.id)

    # view/send/create in BOTH channels, deliberately WITHOUT threads.manage,
    # so visibility is never the reason a cross-channel manage check fails.
    role = servers.create_role(
        owner.id,
        server.id,
        f"member-{unique}",
        permissions={
            "channels.view": True,
            "messages.send": True,
            "threads.create_public": True,
        },
    )
    servers.assign_role(owner.id, server.id, grantee.id, role.id)

    channel_a = servers.create_channel(owner.id, server.id, f"chan-a-{unique}")
    channel_b = servers.create_channel(owner.id, server.id, f"chan-b-{unique}")

    from src.core.threads.manager import ThreadManager

    threads = ThreadManager(
        modules.threads._db,
        auth_module=modules.auth,
        messaging_module=modules.messaging,
        servers_module=servers,
    )
    thread_a = threads.create_thread(owner.id, channel_a.id, f"thread-a-{unique}")
    thread_b = threads.create_thread(owner.id, channel_b.id, f"thread-b-{unique}")

    # Owner grants threads.manage in channel A ONLY, via a real channel override.
    servers.set_channel_override(
        owner.id,
        channel_a.id,
        "member",
        grantee.id,
        allow={"threads.manage": True},
    )
    return servers, threads, server, grantee, channel_a, channel_b, thread_a, thread_b


def test_threads_manage_grant_is_channel_bounded(two_channel_scenario):
    (
        servers,
        threads,
        server,
        grantee,
        channel_a,
        channel_b,
        thread_a,
        thread_b,
    ) = two_channel_scenario

    # The grant is genuinely channel-scoped, not server-scoped.
    assert not servers.get_permissions(grantee.id, server.id).get("threads.manage")
    assert servers.get_permissions(grantee.id, server.id, channel_a.id).get(
        "threads.manage"
    )
    assert not servers.get_permissions(grantee.id, server.id, channel_b.id).get(
        "threads.manage"
    )

    # Bounded positive control: works in the granted channel.
    assert threads.can_manage_thread(grantee.id, thread_a.id) is True
    # Cross-channel: the same grant must NOT reach a thread in channel B.
    assert threads.can_manage_thread(grantee.id, thread_b.id) is False


def test_channel_only_grant_cannot_mutate_cross_channel_thread(two_channel_scenario):
    (
        servers,
        threads,
        server,
        grantee,
        channel_a,
        channel_b,
        thread_a,
        thread_b,
    ) = two_channel_scenario

    def row(thread_id):
        return threads._db.fetch_one(
            "SELECT state, locked, name, deleted FROM thread_threads WHERE id = ?",
            (thread_id,),
        )

    baseline = dict(row(thread_b.id))

    # Every destructive thread operation funnels through can_manage_thread; a
    # channel-A-only grant must not touch the channel-B thread, with no write.
    for op in (
        lambda: threads.archive_thread(grantee.id, thread_b.id),
        lambda: threads.lock_thread(grantee.id, thread_b.id),
        lambda: threads.update_thread(grantee.id, thread_b.id, name="pwned"),
        lambda: threads.delete_thread(grantee.id, thread_b.id),
    ):
        with pytest.raises(PermissionDeniedError):
            op()
    assert dict(row(thread_b.id)) == baseline

    # And it cannot perform a server-level channel write with this grant.
    with pytest.raises(servers.PermissionDeniedError):
        servers.delete_channel(grantee.id, channel_b.id)
    assert servers.get_channel(channel_b.id, grantee.id) is not None


def test_channel_only_grant_still_works_in_granted_channel(two_channel_scenario):
    (
        servers,
        threads,
        server,
        grantee,
        channel_a,
        channel_b,
        thread_a,
        thread_b,
    ) = two_channel_scenario

    # No over-tightening: the bounded grant remains usable in channel A.
    assert threads.archive_thread(grantee.id, thread_a.id).state == threads.ThreadState.ARCHIVED
    assert threads.lock_thread(grantee.id, thread_a.id).locked is True
