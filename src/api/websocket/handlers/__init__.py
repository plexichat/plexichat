"""
Opcode handlers - Handle incoming gateway messages.

This module aggregates opcode handlers from sub-modules:
- connection.py: Connection-related handlers (heartbeat, identify, resume)
- presence.py: Presence-related handlers (presence_update, typing)
- voice.py: Voice-related handlers (voice state, WebRTC signaling)
- guild.py: Guild-related handlers (request guild members)
"""

from typing import Optional, Dict, Any, Tuple, TYPE_CHECKING

from src.api.websocket.opcodes import GatewayOpcode, GatewayCloseCode
from src.api.websocket.connection import Connection
from src.api.websocket.session import SessionManager

if TYPE_CHECKING:
    from src.core.auth.manager import AuthManager
    from src.core.presence.manager import PresenceManager
    from src.core.servers.manager import ServerManager


class OpcodeHandler:
    """Main opcode handler that delegates to specialized handlers."""

    def __init__(
        self,
        session_manager: SessionManager,
        auth_module: Optional["AuthManager"] = None,
        presence_module: Optional["PresenceManager"] = None,
        servers_module: Optional["ServerManager"] = None,
    ):
        """
        Initialize the opcode handler.

        Args:
            session_manager: Session manager instance
            auth_module: Auth module for token verification
            presence_module: Presence module for status updates
            servers_module: Servers module for guild data
        """
        self._session_manager = session_manager
        self._auth: Optional["AuthManager"] = auth_module
        self._presence: Optional["PresenceManager"] = presence_module
        self._servers: Optional["ServerManager"] = servers_module

        # Import specialized handlers
        from .connection import ConnectionHandler
        from .presence import PresenceHandler
        from .voice import VoiceHandler
        from .guild import GuildHandler

        self._connection_handler = ConnectionHandler(
            session_manager, auth_module, presence_module
        )
        self._presence_handler = PresenceHandler(presence_module)
        self._voice_handler = VoiceHandler(servers_module)
        self._guild_handler = GuildHandler(servers_module)

    async def handle(
        self,
        connection: Connection,
        opcode: int,
        data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """
        Handle an incoming opcode.

        Args:
            connection: Connection that sent the message
            opcode: Gateway opcode
            data: Payload data

        Returns:
            Tuple of (response_opcode, response_data, close_code)
            close_code is set if connection should be closed
        """
        try:
            op = GatewayOpcode(opcode)
        except ValueError:
            return None, None, int(GatewayCloseCode.UNKNOWN_OPCODE)

        if op == GatewayOpcode.HEARTBEAT:
            return await self._connection_handler.handle_heartbeat(connection, data)
        elif op == GatewayOpcode.IDENTIFY:
            return await self._connection_handler.handle_identify(connection, data)
        elif op == GatewayOpcode.RESUME:
            return await self._connection_handler.handle_resume(connection, data)
        elif op == GatewayOpcode.PRESENCE_UPDATE:
            return await self._presence_handler.handle_presence_update(connection, data)
        elif op == GatewayOpcode.VOICE_STATE_UPDATE:
            return await self._voice_handler.handle_voice_state_update(connection, data)
        elif op == GatewayOpcode.REQUEST_GUILD_MEMBERS:
            return await self._guild_handler.handle_request_guild_members(
                connection, data
            )
        elif op == GatewayOpcode.VOICE_CONNECT:
            return await self._voice_handler.handle_voice_connect(connection, data)
        elif op == GatewayOpcode.VOICE_DISCONNECT:
            return await self._voice_handler.handle_voice_disconnect(connection, data)
        elif op == GatewayOpcode.VOICE_SDP_OFFER:
            return await self._voice_handler.handle_voice_sdp_offer(connection, data)
        elif op == GatewayOpcode.VOICE_SDP_ANSWER:
            return await self._voice_handler.handle_voice_sdp_answer(connection, data)
        elif op == GatewayOpcode.VOICE_ICE_CANDIDATE:
            return await self._voice_handler.handle_voice_ice_candidate(
                connection, data
            )
        elif op == GatewayOpcode.VOICE_SPEAKING:
            return await self._voice_handler.handle_voice_speaking(connection, data)
        elif op == GatewayOpcode.VOICE_QUALITY:
            return await self._voice_handler.handle_voice_quality(connection, data)
        elif op == GatewayOpcode.TYPING_START:
            return await self._presence_handler.handle_typing_start(connection, data)
        elif op == GatewayOpcode.TYPING_STOP:
            return await self._presence_handler.handle_typing_stop(connection, data)
        else:
            return None, None, int(GatewayCloseCode.UNKNOWN_OPCODE)
