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

from typing import (
    Awaitable,
    Callable,
    Optional,
    Dict,
    Any,
    List,
    Tuple,
    TYPE_CHECKING,
    cast,
)

import time

import utils.logger as logger

from src.api.websocket.opcodes import (
    GatewayOpcode,
    GatewayCloseCode,
    CLIENT_OPCODES,
    SERVER_ONLY_OPCODES,
    coerce_opcode,
)
from src.api.websocket.boundaries import (
    GLOBAL_FRAME_BUCKET_KEY,
    INBOUND_FRAME_WINDOW_S,
    MAX_INBOUND_FRAMES_PER_CONNECTION,
    requires_authentication,
)
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
#
# The voice/fabric opcodes below are equally expensive and were previously
# unthrottled: VOICE_CONNECT / SDP / ICE allocate SFU rooms, peers and signed
# media tokens, and ARTIFACT_OP / SUM_OP append to an ordered ops log before
# fan-out (ARTIFACT_SUBSCRIBE additionally rebuilds a 500-op replay per frame).
# A single identified socket could otherwise loop them to exhaust the media
# plane and the database. The ceilings are per-connection sliding windows;
# legitimate clients stay far below them (a tab joins voice a few times per
# minute, trickle ICE bursts are bounded and OT flushes are well under 20/s).
# (max events, window seconds)
INBOUND_RATE_LIMITS: Dict[int, Tuple[int, float]] = {
    int(GatewayOpcode.PRESENCE_UPDATE): (5, 10.0),
    int(GatewayOpcode.TYPING_START): (10, 10.0),
    int(GatewayOpcode.REQUEST_GUILD_MEMBERS): (2, 10.0),
    int(GatewayOpcode.HEARTBEAT): (10, 10.0),
    # Media-plane resource allocation.
    int(GatewayOpcode.VOICE_CONNECT): (5, 10.0),
    int(GatewayOpcode.VOICE_SDP_OFFER): (10, 10.0),
    int(GatewayOpcode.VOICE_SDP_ANSWER): (10, 10.0),
    int(GatewayOpcode.VOICE_ICE_CANDIDATE): (50, 10.0),
    # These four were the remaining unthrottled mutating opcodes that still
    # perform a synchronous DB write and/or a full member fan-out. Without a
    # ceiling an identified socket can loop them to amplify DB/threadpool
    # work and peer frames without bound. The ceilings are deliberately
    # generous: mute/deafen toggles, channel teardown, typing-stop and a
    # client's periodic quality hint all stay well below them in normal use.
    # VOICE_SPEAKING is intentionally absent: it is high-frequency media
    # keepalive (tens of frames per second) and is bounded by the signaling
    # idle sweeper instead.
    int(GatewayOpcode.VOICE_STATE_UPDATE): (20, 10.0),
    int(GatewayOpcode.VOICE_DISCONNECT): (10, 10.0),
    int(GatewayOpcode.TYPING_STOP): (10, 10.0),
    int(GatewayOpcode.VOICE_QUALITY): (10, 10.0),
    # Authentication opcodes are cheap to abuse and expensive to service.
    # IDENTIFY runs ticket verification plus ~5 threadpool DB reads (user,
    # presence, custom status, guild list) and a friend fan-out on every frame;
    # RESUME verifies a ticket and then re-sends the whole bounded replay buffer
    # (up to 100 frames of 256 KiB each) on *every* frame. Both are reachable
    # before authentication and neither had a per-connection ceiling, so a
    # single socket could loop them for unbounded DB/hash/bandwidth work. The
    # limits are generous: a client identifies/resumes a handful of times per
    # minute at most.
    int(GatewayOpcode.IDENTIFY): (5, 10.0),
    int(GatewayOpcode.RESUME): (5, 10.0),
    # Ordered-log writes + fan-out / replay snapshot builds.
    int(GatewayOpcode.ARTIFACT_SUBSCRIBE): (20, 10.0),
    int(GatewayOpcode.ARTIFACT_OP): (200, 10.0),
    int(GatewayOpcode.SUM_SUBSCRIBE): (20, 10.0),
    int(GatewayOpcode.SUM_OP): (100, 10.0),
    # SUM_UNSUBSCRIBE re-runs reauthorize_subscriptions() on every frame:
    # ~3 synchronous DB reads per held workbook subscription (up to 128),
    # so an identified socket can loop it for unbounded DB/threadpool work.
    int(GatewayOpcode.SUM_UNSUBSCRIBE): (20, 10.0),
    # ARTIFACT_UNSUBSCRIBE re-runs reauthorize_subscriptions() on every
    # frame: get_artifact() plus a permission/membership lookup per held
    # subscription (up to 128), so a looped socket amplifies DB/threadpool
    # work without bound. Same gap as SUM_UNSUBSCRIBE, closed the same way.
    int(GatewayOpcode.ARTIFACT_UNSUBSCRIBE): (20, 10.0),
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


# PERF: ``OpcodeHandler.handle`` runs on EVERY inbound gateway frame. Routing
# used to walk a ~21-branch ``if/elif`` chain, so a late-chain opcode paid 21
# equality comparisons per frame and a client flooding those opcodes amplified
# the linear cost without bound. The table below makes dispatch O(1) regardless
# of where the opcode sits. It is built once at import time and read-only after.
#
# Each entry is:
#   (sub-handler attribute, method name, pass dispatcher, require dispatcher,
#    no-dispatcher warning suffix)
# ``require dispatcher`` opcodes are soft-rejected (ignored + logged, connection
# kept) when no dispatcher is wired -- hard-closing would push healthy clients
# into an auth/reconnect loop for a server-side config gap. ``pass dispatcher``
# without ``require dispatcher`` covers ARTIFACT_SUBSCRIBE, which historically
# receives a possibly-None dispatcher without a guard; behaviour is preserved.
_OpcodeRoute = Tuple[str, str, bool, bool, str]
_HandlerResult = Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]
_DispatchMethod = Callable[..., Awaitable[_HandlerResult]]

_OPCODE_DISPATCH_TABLE: Dict[GatewayOpcode, _OpcodeRoute] = {
    GatewayOpcode.HEARTBEAT: (
        "_connection_handler",
        "handle_heartbeat",
        False,
        False,
        "",
    ),
    GatewayOpcode.IDENTIFY: (
        "_connection_handler",
        "handle_identify",
        False,
        False,
        "",
    ),
    GatewayOpcode.RESUME: ("_connection_handler", "handle_resume", False, False, ""),
    GatewayOpcode.PRESENCE_UPDATE: (
        "_presence_handler",
        "handle_presence_update",
        False,
        False,
        "",
    ),
    GatewayOpcode.TYPING_START: (
        "_presence_handler",
        "handle_typing_start",
        False,
        False,
        "",
    ),
    GatewayOpcode.VOICE_STATE_UPDATE: (
        "_voice_handler",
        "handle_voice_state_update",
        False,
        False,
        "",
    ),
    GatewayOpcode.REQUEST_GUILD_MEMBERS: (
        "_guild_handler",
        "handle_request_guild_members",
        False,
        False,
        "",
    ),
    GatewayOpcode.VOICE_CONNECT: (
        "_voice_handler",
        "handle_voice_connect",
        False,
        False,
        "",
    ),
    GatewayOpcode.VOICE_DISCONNECT: (
        "_voice_handler",
        "handle_voice_disconnect",
        False,
        False,
        "",
    ),
    GatewayOpcode.VOICE_SDP_OFFER: (
        "_voice_handler",
        "handle_voice_sdp_offer",
        False,
        False,
        "",
    ),
    GatewayOpcode.VOICE_SDP_ANSWER: (
        "_voice_handler",
        "handle_voice_sdp_answer",
        False,
        False,
        "",
    ),
    GatewayOpcode.VOICE_ICE_CANDIDATE: (
        "_voice_handler",
        "handle_voice_ice_candidate",
        False,
        False,
        "",
    ),
    GatewayOpcode.VOICE_SPEAKING: (
        "_voice_handler",
        "handle_voice_speaking",
        False,
        False,
        "",
    ),
    GatewayOpcode.VOICE_QUALITY: (
        "_voice_handler",
        "handle_voice_quality",
        False,
        False,
        "",
    ),
    GatewayOpcode.TYPING_STOP: (
        "_presence_handler",
        "handle_typing_stop",
        False,
        False,
        "",
    ),
    GatewayOpcode.ARTIFACT_SUBSCRIBE: (
        "_artifact_handler",
        "handle_artifact_subscribe",
        True,
        False,
        "",
    ),
    GatewayOpcode.ARTIFACT_UNSUBSCRIBE: (
        "_artifact_handler",
        "handle_artifact_unsubscribe",
        False,
        False,
        "",
    ),
    # Ordered-log writes + fan-out: a missing dispatcher is a config gap, so
    # soft-reject rather than force a reconnect loop (see comment above).
    GatewayOpcode.ARTIFACT_OP: (
        "_artifact_handler",
        "handle_artifact_op",
        True,
        True,
        "frame ignored",
    ),
    GatewayOpcode.SUM_SUBSCRIBE: (
        "_sum_handler",
        "handle_sum_subscribe",
        True,
        True,
        "request denied",
    ),
    GatewayOpcode.SUM_UNSUBSCRIBE: (
        "_sum_handler",
        "handle_sum_unsubscribe",
        False,
        False,
        "",
    ),
    GatewayOpcode.SUM_OP: (
        "_sum_handler",
        "handle_sum_op",
        True,
        True,
        "frame ignored",
    ),
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
            except Exception as member_error:
                logger.debug(f"member visibility check failed: {member_error}")
                continue

        return visible_user_ids

    def _allow_inbound(self, connection: Connection, opcode: int) -> bool:
        """Cheap per-connection sliding-window admission for one inbound frame.

        Every frame first consumes an aggregate, opcode-independent budget so
        the overall frame COUNT is bounded even for soft-rejected opcodes that
        have no per-opcode ceiling. Frames whose opcode is expensive then also
        consume their per-opcode budget. Both checks fail closed (an
        over-budget frame is dropped, never admitted).
        """
        now = time.monotonic()

        # Defensive: the real Connection always defines the bucket map, but a
        # test double or an alternate transport may not. Never let a missing
        # attribute turn a throttle check into an unhandled exception (which
        # would bypass the gate entirely at the caller).
        buckets = getattr(connection, "_inbound_rate_buckets", None)
        if buckets is None:
            buckets = {}
            try:
                connection._inbound_rate_buckets = buckets
            except Exception:  # pragma: no cover - read-only stub
                pass

        # Aggregate ceiling: applies to ALL frames, including opcodes with no
        # per-opcode limit. Without it a client can loop max-size frames at
        # line rate; per-opcode ceilings alone do not bound the count.
        global_bucket = buckets.setdefault(GLOBAL_FRAME_BUCKET_KEY, [])
        while global_bucket and now - global_bucket[0] > INBOUND_FRAME_WINDOW_S:
            global_bucket.pop(0)
        if len(global_bucket) >= MAX_INBOUND_FRAMES_PER_CONNECTION:
            return False
        global_bucket.append(now)

        limit = INBOUND_RATE_LIMITS.get(int(opcode))
        if limit is None:
            return True

        max_events, window = limit
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
            op = GatewayOpcode(coerce_opcode(opcode))
        except ValueError:
            return None, None, int(GatewayCloseCode.UNKNOWN_OPCODE)

        # Enforce the client opcode allowlist. Server-push-only opcodes are
        # still soft-rejected below (echoes are dropped, not hard-closed); a
        # value in neither set is a protocol violation.
        if int(op) not in CLIENT_OPCODES and int(op) not in SERVER_ONLY_OPCODES:
            return None, None, int(GatewayCloseCode.UNKNOWN_OPCODE)

        # Per-connection admission runs BEFORE any handler dispatch, for EVERY
        # opcode. The aggregate frame ceiling applies to all frames (including
        # soft-rejected ones); expensive opcodes additionally consume their
        # per-opcode budget. Over-budget frames are dropped (no close) so a
        # burst cannot amplify into CPU/DB/threadpool work or tear the socket.
        if not self._allow_inbound(connection, op):
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

        # Identity gate for every state-mutating opcode. Centralizing it here
        # means a newly added mutating handler cannot forget its auth check;
        # resource-level authorization is still enforced by the owning
        # manager inside the handler.
        if requires_authentication(int(op)) and not connection.is_authenticated:
            logger.debug(
                f"Connection {connection.connection_id}: unauthenticated "
                f"mutating opcode {op} rejected"
            )
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        # O(1) dispatch: one table lookup replaces the old linear chain that
        # compared every frame against ~21 opcodes. A missing entry fails
        # closed with UNKNOWN_OPCODE, exactly like the old ``else`` branch.
        route = _OPCODE_DISPATCH_TABLE.get(op)
        if route is None:
            return None, None, int(GatewayCloseCode.UNKNOWN_OPCODE)

        handler_attr, method_name, pass_dispatcher, require_dispatcher, warning = route
        if require_dispatcher and self._dispatcher is None:
            # SOFT REJECT: a wiring/config gap must not kill healthy
            # connections with UNKNOWN_ERROR 4000; clients would reconnect-loop
            # straight into the same condition. Ignore and log so the session
            # survives until the dispatcher is available.
            logger.warning(
                f"Connection {connection.connection_id}: {op.name} received "
                f"but no gateway dispatcher is configured; {warning}"
            )
            return None, None, None

        method = cast(
            "_DispatchMethod", getattr(getattr(self, handler_attr), method_name)
        )
        if pass_dispatcher:
            return await method(connection, data, self._dispatcher)
        return await method(connection, data)
