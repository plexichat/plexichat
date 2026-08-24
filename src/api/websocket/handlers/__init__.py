"""
Opcode handlers - Handle incoming gateway messages.

This module aggregates opcode handlers from sub-modules:
- connection.py: Connection-related handlers (heartbeat, identify, resume)
- presence.py: Presence-related handlers (presence_update, typing)
- voice.py: Voice-related handlers (voice state, WebRTC signaling)
- guild.py: Guild-related handlers (request guild members)
- artifacts.py: Artifact real-time fabric handlers (subscribe, op, sync)
- sum.py: PlexiSum real-time fabric handlers (workbook subscribe, op, sync)
"""

from typing import Optional, Dict, Any, List, Tuple, TYPE_CHECKING

import time

import utils.logger as logger

from src.api.websocket.opcodes import GatewayOpcode, GatewayCloseCode
from src.api.websocket.connection import Connection
from src.api.websocket.session import SessionManager

if TYPE_CHECKING:
    from src.core.auth.manager import AuthManager
    from src.core.presence.manager import PresenceManager
    from src.core.servers.manager import ServerManager
    from src.api.websocket.dispatcher import GatewayDispatcher


# CORRECTNESS FIX: inbound per-connection throttling for opcodes that each
# trigger synchronous DB writes followed by a fan-out to every friend and
# shared-server member. Without this, a malicious client looping these
# frames produces unbounded DB write amplification and event construction.
# (max events, window seconds)
INBOUND_RATE_LIMITS: Dict[int, Tuple[int, float]] = {
    int(GatewayOpcode.PRESENCE_UPDATE): (5, 10.0),
    int(GatewayOpcode.TYPING_START): (10, 10.0),
}


class OpcodeHandler:
    """Main opcode handler that delegates to specialized handlers."""

    def __init__(
        self,
        session_manager: SessionManager,
        auth_module: Optional["AuthManager"] = None,
        presence_module: Optional["PresenceManager"] = None,
        servers_module: Optional["ServerManager"] = None,
        dispatcher: Optional["GatewayDispatcher"] = None,
    ):
        """
        Initialize the opcode handler.

        Args:
            session_manager: Session manager instance
            auth_module: Auth module for token verification
            presence_module: Presence module for status updates
            servers_module: Servers module for guild data
            dispatcher: Gateway dispatcher (used by artifact relay)
        """
        self._session_manager = session_manager
        self._auth: Optional["AuthManager"] = auth_module
        self._presence: Optional["PresenceManager"] = presence_module
        self._servers: Optional["ServerManager"] = servers_module
        self._dispatcher: Optional["GatewayDispatcher"] = dispatcher

        # Import specialized handlers
        from .connection import ConnectionHandler
        from .presence import PresenceHandler
        from .voice import VoiceHandler
        from .guild import GuildHandler
        from .artifacts import ArtifactHandler
        from .sum import SumHandler

        self._connection_handler = ConnectionHandler(
            session_manager, auth_module, presence_module, servers_module
        )
        self._presence_handler = PresenceHandler(presence_module)
        self._voice_handler = VoiceHandler(servers_module)
        self._guild_handler = GuildHandler(servers_module)
        self._artifact_handler = ArtifactHandler()
        self._sum_handler = SumHandler()

    def _get_typing_recipient_ids(
        self,
        user_id: int,
        channel_id: int,
        server_id: int,
        include_self: bool = False,
    ) -> List[int]:
        """Return only members who can still view a channel.

        Typing indicators (and other presence-style events) must never leak
        to users who lost access to the channel; recipients are resolved
        through the servers boundary and filtered by channel visibility.
        """
        if not self._servers:
            return []

        member_user_ids = self._servers.get_member_user_ids(
            server_id, exclude_user_id=None if include_self else user_id
        )

        visible_user_ids: List[int] = []
        for member_user_id in member_user_ids:
            try:
                if self._servers.get_channel(channel_id, member_user_id):
                    visible_user_ids.append(member_user_id)
            except Exception:
                continue

        return visible_user_ids

    def _allow_inbound(self, connection: Connection, opcode: int) -> bool:
        """Cheap per-connection sliding-window check for rate-limited opcodes."""
        limit = INBOUND_RATE_LIMITS.get(int(opcode))
        if limit is None:
            return True

        max_events, window = limit
        now = time.monotonic()

        buckets = connection._inbound_rate_buckets

        timestamps = buckets.setdefault(int(opcode), [])
        while timestamps and now - timestamps[0] > window:
            timestamps.pop(0)

        if len(timestamps) >= max_events:
            return False

        timestamps.append(now)
        return True

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
        elif int(op) in INBOUND_RATE_LIMITS:
            # Drop silently (no close) when the connection exceeds its
            # inbound budget for presence/typing frames.
            if not self._allow_inbound(connection, op):
                logger.debug(
                    f"Connection {connection.connection_id}: "
                    f"rate-limited inbound opcode {op}"
                )
                return None, None, None
            if op == GatewayOpcode.PRESENCE_UPDATE:
                return await self._presence_handler.handle_presence_update(
                    connection, data
                )
            return await self._presence_handler.handle_typing_start(connection, data)
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
        elif op == GatewayOpcode.TYPING_STOP:
            return await self._presence_handler.handle_typing_stop(connection, data)
        elif op == GatewayOpcode.ARTIFACT_SUBSCRIBE:
            return await self._artifact_handler.handle_artifact_subscribe(
                connection, data
            )
        elif op == GatewayOpcode.ARTIFACT_UNSUBSCRIBE:
            return await self._artifact_handler.handle_artifact_unsubscribe(
                connection, data
            )
        elif op == GatewayOpcode.ARTIFACT_OP:
            if self._dispatcher is None:
                return None, None, int(GatewayCloseCode.UNKNOWN_ERROR)
            return await self._artifact_handler.handle_artifact_op(
                connection, data, self._dispatcher
            )
        elif op == GatewayOpcode.SUM_SUBSCRIBE:
            if self._dispatcher is None:
                return None, None, int(GatewayCloseCode.UNKNOWN_ERROR)
            return await self._sum_handler.handle_sum_subscribe(
                connection, data, self._dispatcher
            )
        elif op == GatewayOpcode.SUM_UNSUBSCRIBE:
            return await self._sum_handler.handle_sum_unsubscribe(connection, data)
        elif op == GatewayOpcode.SUM_OP:
            if self._dispatcher is None:
                return None, None, int(GatewayCloseCode.UNKNOWN_ERROR)
            return await self._sum_handler.handle_sum_op(
                connection, data, self._dispatcher
            )
        else:
            return None, None, int(GatewayCloseCode.UNKNOWN_OPCODE)
