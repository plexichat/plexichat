"""Session-setup DB lookup dedupe for gateway IDENTIFY.

A single IDENTIFY builds the READY payload and then dispatches the online
presence transition. Both paths read ``presence.get_status(user_id)``, which is
a raw ``SELECT status FROM pres_presence WHERE user_id = ?`` with no cache of
its own. Before the fix that meant two identical DB queries per connection --
doubling status-query load during a reconnect burst where the same users
re-identify over and over.

The fix threads one bounded, per-identify memo through both paths so the status
is read once. The memo is created fresh for each IDENTIFY and keyed by user id,
so it can never be shared across users (no cross-user leak), across connections
or across workers (multi-node safe). It only memoises a display/status read --
never a token/permission decision -- so authz is unchanged.
"""

from unittest.mock import Mock

import pytest

from src.api.websocket.connection import Connection
from src.api.websocket.handlers.connection import ConnectionHandler
from src.api.websocket.opcodes import GatewayOpcode
from src.core.presence.models import UserStatus


@pytest.mark.asyncio
async def test_identify_reads_presence_status_once(
    monkeypatch,
    session_manager,
    mock_auth_module,
    mock_websocket,
    sample_identify_payload,
):
    """A single IDENTIFY issues exactly one presence-status DB read."""
    # Keep the online-transition dispatch from touching process globals / DB.
    import src.api.routes.presence as presence_route

    monkeypatch.setattr(
        presence_route, "_get_presence_targets", lambda user_id: [], raising=True
    )

    presence = Mock()
    presence.get_status.return_value = UserStatus.ONLINE
    presence.get_presence.return_value = None
    presence.set_status.return_value = None

    handler = ConnectionHandler(
        session_manager=session_manager,
        auth_module=mock_auth_module,
        presence_module=presence,
        servers_module=Mock(get_servers=Mock(return_value=[])),
    )
    conn = Connection(
        websocket=mock_websocket,
        connection_id=session_manager.generate_connection_id(),
        heartbeat_interval_ms=45000,
    )
    session_manager.add_connection(conn)

    op, data, close = await handler.handle_identify(conn, sample_identify_payload)

    assert close is None
    assert op == GatewayOpcode.DISPATCH
    assert data["t"] == "READY"
    # The READY enrichment and the online-transition dispatch must share the
    # same status read: one query per IDENTIFY, not two.
    assert presence.get_status.call_count == 1
    assert presence.get_status.call_args.args == (12345,)


@pytest.mark.asyncio
async def test_identify_status_memo_is_per_user(
    monkeypatch,
    session_manager,
    mock_websocket,
    sample_identify_payload,
):
    """Distinct users in distinct IDENTIFY calls never share a memo entry."""
    import src.api.routes.presence as presence_route

    monkeypatch.setattr(
        presence_route, "_get_presence_targets", lambda user_id: [], raising=True
    )

    from src.core.auth.models import AccountType, TokenInfo

    auth = Mock()
    seen_users = []

    def _verify(token, ip_address=None, user_agent=None, is_selftest=False):
        uid = seen_users[-1]
        return TokenInfo(
            valid=True,
            token_type="user",
            account_id=uid,
            user_id=uid,
            session_id="auth-session",
            permissions={},
            rate_limit_tier="free",
            expires_at=None,
            username="u",
            account_type=AccountType.USER,
        )

    auth.verify_token.side_effect = _verify
    auth.get_user.side_effect = lambda uid: None

    presence = Mock()
    presence.get_status.return_value = UserStatus.ONLINE
    presence.get_presence.return_value = None
    presence.set_status.return_value = None

    handler = ConnectionHandler(
        session_manager=session_manager,
        auth_module=auth,
        presence_module=presence,
        servers_module=Mock(get_servers=Mock(return_value=[])),
    )

    for uid in (111, 222):
        seen_users.append(uid)
        conn = Connection(
            websocket=mock_websocket,
            connection_id=session_manager.generate_connection_id(),
            heartbeat_interval_ms=45000,
        )
        session_manager.add_connection(conn)
        op, data, close = await handler.handle_identify(conn, sample_identify_payload)
        assert close is None
        assert op == GatewayOpcode.DISPATCH

    # Two users x one read each == two reads; each keyed by its own user id.
    assert presence.get_status.call_count == 2
    assert [c.args for c in presence.get_status.call_args_list] == [(111,), (222,)]
