"""The slowmode gate must reuse the channel the send path already resolved.

Every send to a server channel resolves that channel once (REST: via
``_resolve_server_id``; core: at the top of ``send_channel_message``) and then
asks ``check_channel_slowmode`` whether the user is inside their interval.
The gate used to resolve the channel a *second* time. ``get_channel`` issues an
uncached ``SELECT 1 FROM srv_members`` membership probe (plus a channel-view
permission check) on every call, so each slowmode check cost a duplicate DB
round-trip. The gate now accepts the already-resolved channel and skips it.
"""

import uuid

import pytest


@pytest.fixture
def slowmode_channel(modules, user_pool):
    owner = user_pool.get_user()
    member = user_pool.get_user()
    servers = modules.servers
    server = servers.create_server(
        owner_id=owner.id, name=f"Slowmode Server {uuid.uuid4().hex[:6]}"
    )
    servers.add_member(server.id, member.id)
    channel = servers.create_channel(
        user_id=owner.id,
        server_id=server.id,
        name=f"slow-{uuid.uuid4().hex[:6]}",
        slowmode_seconds=30,
    )
    return servers, channel, owner, member


def test_slowmode_check_reuses_resolved_channel(slowmode_channel, monkeypatch):
    servers, channel, _owner, member = slowmode_channel

    real_get_channel = servers.get_channel
    calls = []

    def counting_get_channel(cid, uid):
        calls.append((int(cid), int(uid)))
        return real_get_channel(cid, uid)

    monkeypatch.setattr(servers, "get_channel", counting_get_channel)

    # The send path resolves the channel once.
    resolved = servers.get_channel(channel.id, member.id)
    assert resolved is not None
    baseline = len(calls)

    # First gate call seeds the window; the second call is rate limited.
    assert (
        servers.check_channel_slowmode(member.id, channel.id, channel=resolved) is None
    )
    assert len(calls) == baseline, (
        f"first gate call re-resolved the channel ({baseline} -> {len(calls)})"
    )

    retry_after = servers.check_channel_slowmode(
        member.id, channel.id, channel=resolved
    )
    assert retry_after is not None and 0 < retry_after <= 30
    assert len(calls) == baseline, (
        f"second gate call re-resolved the channel ({baseline} -> {len(calls)})"
    )


def test_slowmode_check_fails_closed_on_mismatched_channel(
    slowmode_channel, monkeypatch
):
    """A caller cannot swap in a mismatched channel object to dodge slowmode.

    The gate only trusts a pre-resolved channel whose id matches the channel
    being posted to; anything else triggers an authoritative re-lookup so the
    interval and the membership/authz check come from the real channel row.
    """
    servers, channel, owner, member = slowmode_channel

    other = servers.create_channel(
        user_id=owner.id,
        server_id=channel.server_id,
        name=f"other-{uuid.uuid4().hex[:6]}",
        slowmode_seconds=0,
    )

    real_get_channel = servers.get_channel
    calls = []

    def counting_get_channel(cid, uid):
        calls.append((int(cid), int(uid)))
        return real_get_channel(cid, uid)

    monkeypatch.setattr(servers, "get_channel", counting_get_channel)

    # ``other`` has slowmode disabled; trusting it would skip both the real
    # interval and the authoritative membership lookup.
    assert servers.check_channel_slowmode(member.id, channel.id, channel=other) is None
    assert any(
        cid == int(channel.id) and uid == int(member.id) for cid, uid in calls
    ), "mismatched channel must trigger an authoritative re-lookup"
