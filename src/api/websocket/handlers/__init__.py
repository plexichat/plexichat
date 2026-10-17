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
# REQUEST_GUILD_MEMBERS fans out one threadpool DB lookup PER MEMBER and
# HEARTBEAT updates liveness bookkeeping, so both are throttled too.
# (max events, window seconds)
INBOUND_RATE_LIMITS: Dict[int, Tuple[int, float]] = {
    int(GatewayOpcode.PRESENCE_UPDATE): (5, 10.0),
    int(GatewayOpcode.TYPING_START): (10, 10.0),
    int(GatewayOpcode.REQUEST_GUILD_MEMBERS): (2, 10.0),
    int(GatewayOpcode.HEARTBEAT): (10, 10.0),
}

# Client-sendable extension opcodes that have no server-side consumer yet,
# plus server-push-only opcodes echoed back by misbehaving clients. These
# are SOFT-REJECTED (ignored + logged): tearing the socket down for an
# echo or an unimplemented extension forces healthy clients into
# reconnect loops for something they can recover from in-session.
IGNORED_OPCODES = frozenset(
    {
        GatewayOpcode.SERVER_STATUS,
        GatewayOpcode.VERSION_CHECK,
        GatewayOpcode.INTERACTION_CREATE,
        GatewayOpcode.INTERACTION_RESPONSE,
        GatewayOpcode.RATCHET_UPDATE,
        # Server-push-only opcodes that must never arrive from a client;
        # echoes are dropped instead of hard-closing the connection.
        GatewayOpcode.DISPATCH,
        GatewayOpcode.RECONNECT,
        GatewayOpcode.INVALID_SESSION,
        GatewayOpcode.HELLO,
        GatewayOpcode.HEARTBEAT_ACK,
        GatewayOpcode.ARTIFACT_SYNC,
        GatewayOpcode.SUM_SYNC,
        # ``ARTIFACT_OP_REJECTED`` (68) is a rejection frame the server emits
        # for a stale ``ARTIFACT_OP``; a client that echoes it back (or that
        # forwards a peer's frame) must not be hard-closed with 4001
        # UNKNOWN_OPCODE -- every other server-push-only opcode above is
        # soft-rejected for exactly this reason, and dropping this one out
        # forces healthy clients into a reconnect loop they cannot recover
        # from in-session.
        GatewayOpcode.ARTIFACT_OP_REJECTED,
    }
)


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

        self._register_voice_offer_sender()

    def _register_voice_offer_sender(self) -> None:
        """Let the signaling manager deliver an SFU-initiated SDP offer.

        aiortc never emits ``negotiationneeded``, so a producer forwarded to a
        peer that has already connected produces an offer that nothing else
        would deliver. Core cannot import this layer, so the manager is handed
        the deliverer here and calls it when an offer needs a client.
        """
        try:
            from src.core.voice import signaling

            signaling.set_offer_sender(self._send_voice_renegotiation_offer)
        except Exception as exc:  # never block gateway construction on this
            logger.debug(f"Could not register the voice offer sender: {exc}")

    async def _send_voice_renegotiation_offer(
        self, user_id: int, channel_id: int, offer_sdp: str
    ) -> bool:
        """Push a server-initiated SDP offer to a user's gateway connections.

        The target is re-checked here against the authoritative voice state:
        the manager already required a connection bound to the channel, but
        the offer must not reach a user who has since left the call.
        """
        try:
            from src.core import voice

            state = voice.get_voice_state(user_id)
            if not state or getattr(state, "channel_id", None) != channel_id:
                logger.debug(
                    f"Dropping renegotiation offer for user {user_id}: not in "
                    f"channel {channel_id}"
                )
                return False
        except Exception as exc:
            logger.warning(
                f"Could not verify voice state for user {user_id} before "
                f"sending a renegotiation offer: {exc}"
            )
            return False

        dispatcher = self._dispatcher
        if dispatcher is None:
            try:
                from src.api.websocket import get_dispatcher

                dispatcher = get_dispatcher()
            except Exception:
                dispatcher = None
        if dispatcher is None:
            logger.debug("No gateway dispatcher: renegotiation offer not sent")
            return False

        try:
            sent = await dispatcher.dispatch_raw_to_users(
                [user_id],
                GatewayOpcode.VOICE_SDP_OFFER,
                {
                    "from_user_id": str(user_id),
                    "channel_id": str(channel_id),
                    "type": "offer",
                    "sdp": offer_sdp,
                    "renegotiation": True,
                },
            )
        except Exception as exc:
            logger.warning(
                f"Failed to send renegotiation offer to user {user_id}: {exc}"
            )
            return False
        return sent > 0

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

        # Cheap per-connection throttle for heavy opcodes runs BEFORE any
        # handler dispatch; over-budget frames are dropped (no close) so a
        # burst cannot amplify into DB/threadpool work or tear the socket.
        if int(op) in INBOUND_RATE_LIMITS and not self._allow_inbound(connection, op):
            logger.debug(
                f"Connection {connection.connection_id}: "
                f"rate-limited inbound opcode {op}"
            )
            return None, None, None

        if op in IGNORED_OPCODES:
            logger.debug(
                f"Connection {connection.connection_id}: soft-rejected "
                f"unrouted/echo opcode {op} (ignored, connection kept)"
            )
            return None, None, None

        if op == GatewayOpcode.HEARTBEAT:
            return await self._connection_handler.handle_heartbeat(connection, data)
        elif op == GatewayOpcode.IDENTIFY:
            return await self._connection_handler.handle_identify(connection, data)
        elif op == GatewayOpcode.RESUME:
            return await self._connection_handler.handle_resume(connection, data)
        elif op == GatewayOpcode.PRESENCE_UPDATE:
            # Throttle already enforced above.
            return await self._presence_handler.handle_presence_update(connection, data)
        elif op == GatewayOpcode.TYPING_START:
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
                connection, data, self._dispatcher
            )
        elif op == GatewayOpcode.ARTIFACT_UNSUBSCRIBE:
            return await self._artifact_handler.handle_artifact_unsubscribe(
                connection, data
            )
        elif op == GatewayOpcode.ARTIFACT_OP:
            if self._dispatcher is None:
                # SOFT REJECT: a wiring/config gap must not kill healthy
                # connections with UNKNOWN_ERROR 4000; clients would
                # reconnect-loop straight into the same condition. Ignore
                # and log so the session survives until the dispatcher is
                # available.
                logger.warning(
                    f"Connection {connection.connection_id}: {op.name} received "
                    f"but no gateway dispatcher is configured; frame ignored"
                )
                return None, None, None
            return await self._artifact_handler.handle_artifact_op(
                connection, data, self._dispatcher
            )
        elif op == GatewayOpcode.SUM_SUBSCRIBE:
            if self._dispatcher is None:
                # Deliberate silent denial (documented on the sum handler):
                # never NOT_AUTHENTICATED / UNKNOWN_ERROR, which would push
                # clients into auth/reconnect loops for a server-side gap.
                logger.warning(
                    f"Connection {connection.connection_id}: SUM_SUBSCRIBE received "
                    f"but no gateway dispatcher is configured; request denied"
                )
                return None, None, None
            return await self._sum_handler.handle_sum_subscribe(
                connection, data, self._dispatcher
            )
        elif op == GatewayOpcode.SUM_UNSUBSCRIBE:
            return await self._sum_handler.handle_sum_unsubscribe(connection, data)
        elif op == GatewayOpcode.SUM_OP:
            if self._dispatcher is None:
                logger.warning(
                    f"Connection {connection.connection_id}: SUM_OP received "
                    f"but no gateway dispatcher is configured; frame ignored"
                )
                return None, None, None
            return await self._sum_handler.handle_sum_op(
                connection, data, self._dispatcher
            )
        else:
            return None, None, int(GatewayCloseCode.UNKNOWN_OPCODE)
