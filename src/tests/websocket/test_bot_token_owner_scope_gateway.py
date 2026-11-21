"""A bot credential must never inherit its owner's gateway audience.

``AuthManager._verify_bot_token`` attaches the bot's OWNER as ``user_id`` and
the bot's own account as ``account_id``.  IDENTIFY used to bind the socket to
``token_info.user_id``, so a bot token identified as the owner and was placed
in every owner-scoped fan-out (READY guilds, friend presence, DMs and any
per-event dispatch keyed on the owner id).  This is the same cross-principal
confusion the REST layer already rejects with ``require_user_token``.

The test below proves the leak with an actual private DM event: with the leak
present BOTH the owner's socket and the bot's socket receive it; after the fix
only the legitimate owner socket does.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.api.websocket.connection import Connection
from src.api.websocket.dispatcher import GatewayDispatcher
from src.api.websocket.handlers import OpcodeHandler
from src.api.websocket.opcodes import GatewayOpcode
from src.api.websocket.session import SessionManager
from src.core.auth.models import AccountType, TokenInfo
from src.core.events.models import Event
from src.core.events.types import EventType, GatewayIntent

pytestmark = pytest.mark.unit

OWNER_ID = 100
BOT_ID = 900
_PRIVATE_DM = Event(
    event_type=EventType.MESSAGE_CREATE,
    data={"channel_id": "555", "content": "owner-only private message"},
    server_id=None,
    channel_id=555,
)


def _token(*, bot: bool) -> TokenInfo:
    if bot:
        return TokenInfo(
            valid=True,
            token_type="bot",
            account_id=BOT_ID,
            user_id=OWNER_ID,  # a bot token's user_id is its owner
            session_id=None,
            permissions={},
            rate_limit_tier="bot",
            expires_at=None,
            username="test-bot",
            account_type=AccountType.BOT,
        )
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=OWNER_ID,
        user_id=OWNER_ID,
        session_id="user-session",
        permissions={},
        rate_limit_tier="standard",
        expires_at=None,
        username="test-owner",
        account_type=AccountType.USER,
    )


def _websocket():
    ws = Mock()
    ws.client = SimpleNamespace(host="127.0.0.1")
    ws.headers = {"User-Agent": "pytest"}
    ws.send_json = AsyncMock()
    ws.send_bytes = AsyncMock()
    ws.close = AsyncMock()
    return ws


async def _identify(auth, session_manager, dispatcher, connection_id, is_bot):
    auth.verify_token.return_value = _token(bot=is_bot)
    handler = OpcodeHandler(
        session_manager=session_manager,
        auth_module=auth,
        servers_module=Mock(get_servers=Mock(return_value=[])),
        dispatcher=dispatcher,
    )
    # Keep IDENTIFY's presence side-effect out of the DB; it is unrelated to
    # the identity binding under test.
    handler._connection_handler._dispatch_online_presence = AsyncMock()

    conn = Connection(websocket=_websocket(), connection_id=connection_id)
    session_manager.add_connection(conn)
    _, _, close_code = await handler.handle(
        conn,
        GatewayOpcode.IDENTIFY,
        {
            "token": "any-ticket",
            "intents": int(GatewayIntent.default_intents()),
            "properties": {},
        },
    )
    assert close_code is None, "legitimate-looking credential must identify"
    conn.websocket.send_json.reset_mock()
    return conn


@pytest.mark.asyncio
async def test_bot_token_cannot_receive_owner_scoped_private_event():
    session_manager = SessionManager()
    dispatcher = GatewayDispatcher(session_manager=session_manager)
    auth = Mock()
    auth.get_user.return_value = SimpleNamespace(
        id=OWNER_ID, username="test-owner", discriminator="0", avatar=None, bot=False
    )

    owner_conn = await _identify(
        auth, session_manager, dispatcher, "owner-conn", is_bot=False
    )
    bot_conn = await _identify(
        auth, session_manager, dispatcher, "bot-conn", is_bot=True
    )

    sent = await dispatcher.dispatch_event(_PRIVATE_DM, [OWNER_ID])

    # Legitimate subscriber control: the owner's own socket still receives it.
    assert owner_conn.websocket.send_json.await_count == 1
    # The bot socket must never see the owner's private DM. Before the fix the
    # bot was registered as the owner, so this socket received the DM too.
    assert bot_conn.websocket.send_json.await_count == 0
    assert sent == 1
    # The bot socket is not registered under the owner's principal at all.
    assert owner_conn.user_id == OWNER_ID
    assert bot_conn.user_id != OWNER_ID
