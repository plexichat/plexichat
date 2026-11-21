"""Timeout enforcement must be reachable on the production servers object.

``src.api.get_servers()`` resolves the public ``src.core.servers`` module
facade in production, not the internal manager used by the test fixtures.
The REST send/edit/react routes only check ``is_timed_out`` when the resolved
object exposes that gate, so a missing proxy made timeout enforcement a silent
no-op on live traffic. These tests pin both the public surface and the actual
send behaviour through the facade.
"""

import time

import pytest

import src.api as api


def test_public_surfaces_expose_timeout_gate():
    import src.core.servers as facade
    from src.core.servers import members as members_facade
    from src.core.servers.composer import ServersManager

    assert callable(getattr(facade, "is_timed_out", None))
    assert callable(getattr(members_facade, "is_timed_out", None))
    assert callable(getattr(ServersManager, "is_timed_out", None))
    assert "is_timed_out" in facade.__all__


@pytest.mark.asyncio
async def test_timed_out_send_denied_through_public_facade(
    test_client,
    db,
    auth_manager,
    messaging_manager,
    server_manager,
    automod_manager,
    test_server,
    test_user_with_token,
    monkeypatch,
):
    # Recreate production wiring: bootstrap/app.py wires api.get_servers() to
    # the public module facade, not to an internal manager instance.
    import src.core.servers as facade
    import src.core.servers.channels as channels_facade
    import src.core.servers.members as members_facade
    import src.core.servers.messages as messages_facade

    facade.setup(db, auth_manager, messaging_manager)
    for mod in (channels_facade, members_facade, messages_facade):
        monkeypatch.setattr(mod, "_manager", facade._manager, raising=False)

    server, owner = test_server
    channel = server_manager.get_channels(owner.id, server.id)[0]
    server_manager.update_member(
        owner.id,
        server.id,
        owner.id,
        timeout_until=int(time.time() * 1000) + 600_000,
        timeout_reason="regression test",
    )
    assert facade.is_timed_out(owner.id, server.id) is True

    monkeypatch.setattr(api, "get_servers", lambda: facade)

    response = test_client.post(
        f"/api/v1/channels/{channel.id}/messages",
        headers={"Authorization": f"Bearer {test_user_with_token['token']}"},
        json={"content": "must be blocked by timeout"},
    )
    assert response.status_code == 403, response.text
