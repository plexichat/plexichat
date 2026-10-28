"""Channel slowmode must gate every send path, not just the core manager.

The REST send route publishes through the messaging module directly, so it
used to skip the servers manager's slowmode gate entirely: a member could post
as fast as they liked in a slow channel.  These tests pin the gate itself and
the route wiring.
"""

import uuid

import pytest


@pytest.fixture
def slowmode_setup(modules, user_pool):
    """A server with a slow channel, its owner, and a plain member."""
    owner = user_pool.get_user()
    member = user_pool.get_user()

    server = modules.servers.create_server(
        owner_id=owner.id, name=f"Slowmode Server {uuid.uuid4().hex[:6]}"
    )
    modules.servers.add_member(server.id, member.id)
    channel = modules.servers.create_channel(
        user_id=owner.id,
        server_id=server.id,
        name=f"slow-{uuid.uuid4().hex[:6]}",
        slowmode_seconds=30,
    )
    return modules.servers, server, channel, owner, member


def test_member_is_limited_and_staff_bypass(slowmode_setup):
    """A plain member waits out the interval; management roles bypass it."""
    servers, _server, channel, owner, member = slowmode_setup

    assert servers.check_channel_slowmode(member.id, channel.id) is None

    retry_after = servers.check_channel_slowmode(member.id, channel.id)
    assert retry_after is not None, "second send inside the window must be limited"
    assert 0 < retry_after <= 30

    # The owner holds administrator, so slowmode never applies to them.
    assert servers.check_channel_slowmode(owner.id, channel.id) is None
    assert servers.check_channel_slowmode(owner.id, channel.id) is None


def test_public_manager_exposes_the_slowmode_gate():
    """The object the API resolves must expose the gate itself.

    The fixtures hand the routes the internal sub-manager, which owns
    ``check_channel_slowmode``. Production resolves the public composer
    ``ServersManager``, which only had the mixins' proxies -- so the route's
    ``getattr`` probe found nothing, skipped enforcement, and logged nothing
    while every test stayed green.
    """
    from src.core.servers.composer import ServersManager

    assert hasattr(ServersManager, "check_channel_slowmode")

    class _Inner:
        def check_channel_slowmode(self, user_id, channel_id):
            return 4.5

    manager = ServersManager.__new__(ServersManager)
    manager._manager = _Inner()
    assert manager.check_channel_slowmode(1, 2) == 4.5


def test_servers_module_exposes_the_slowmode_gate(monkeypatch):
    """The module facade must expose it too (some wiring returns the module)."""
    import src.core.servers as servers_facade
    import src.core.servers.messages as servers_messages

    assert "check_channel_slowmode" in servers_facade.__all__

    class _Stub:
        def check_channel_slowmode(self, user_id, channel_id):
            return 3.5

    monkeypatch.setattr(servers_messages, "_manager", _Stub())
    assert servers_facade.check_channel_slowmode(1, 2) == 3.5


def test_rest_send_rejects_slowmode_violation(
    test_client, modules, test_server, test_user_with_token, monkeypatch
):
    """The send route answers 429 and publishes nothing when limited."""
    import json

    server, _user = test_server
    channel = modules.servers.get_channels(server.owner_id, server.id)[0]
    token = test_user_with_token["token"]
    headers = {"Authorization": f"Bearer {token}"}

    monkeypatch.setattr(
        modules.servers, "check_channel_slowmode", lambda user_id, channel_id: 7.5
    )

    response = test_client.post(
        f"/api/v1/channels/{channel.id}/messages",
        headers=headers,
        json={"content": "too fast"},
    )

    assert response.status_code == 429, response.text
    error = response.json()["error"]
    assert error["retry_after_ms"] == 7500
    assert response.headers["Retry-After"] == "8"

    listing = test_client.get(
        f"/api/v1/channels/{channel.id}/messages",
        headers=headers,
    )
    assert listing.status_code == 200, listing.text
    assert json.dumps(listing.json()).find("too fast") == -1
