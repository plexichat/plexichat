"""
Opcode handlers - Handle incoming gateway messages.
"""

from typing import Optional, Dict, Any, Tuple, List, TYPE_CHECKING

import utils.logger as logger
import src.core.events as events_mod
from starlette.concurrency import run_in_threadpool

from .opcodes import GatewayOpcode, GatewayCloseCode
from .connection import Connection, ConnectionState
from .session import SessionManager
from .intents import validate_intents, DEFAULT_INTENTS
from src.core.auth.models import TokenInfo

if TYPE_CHECKING:
    from src.core.auth.manager import AuthManager
    from src.core.presence.manager import PresenceManager
    from src.core.servers.manager import ServerManager


class OpcodeHandler:
    """Handles incoming gateway opcodes."""

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
            return await self._handle_heartbeat(connection, data)
        elif op == GatewayOpcode.IDENTIFY:
            return await self._handle_identify(connection, data)
        elif op == GatewayOpcode.RESUME:
            return await self._handle_resume(connection, data)
        elif op == GatewayOpcode.PRESENCE_UPDATE:
            return await self._handle_presence_update(connection, data)
        elif op == GatewayOpcode.VOICE_STATE_UPDATE:
            return await self._handle_voice_state_update(connection, data)
        elif op == GatewayOpcode.REQUEST_GUILD_MEMBERS:
            return await self._handle_request_guild_members(connection, data)
        elif op == GatewayOpcode.VOICE_CONNECT:
            return await self._handle_voice_connect(connection, data)
        elif op == GatewayOpcode.VOICE_DISCONNECT:
            return await self._handle_voice_disconnect(connection, data)
        elif op == GatewayOpcode.VOICE_SDP_OFFER:
            return await self._handle_voice_sdp_offer(connection, data)
        elif op == GatewayOpcode.VOICE_SDP_ANSWER:
            return await self._handle_voice_sdp_answer(connection, data)
        elif op == GatewayOpcode.VOICE_ICE_CANDIDATE:
            return await self._handle_voice_ice_candidate(connection, data)
        elif op == GatewayOpcode.VOICE_SPEAKING:
            return await self._handle_voice_speaking(connection, data)
        elif op == GatewayOpcode.VOICE_QUALITY:
            return await self._handle_voice_quality(connection, data)
        elif op == GatewayOpcode.TYPING_START:
            return await self._handle_typing_start(connection, data)
        elif op == GatewayOpcode.TYPING_STOP:
            return await self._handle_typing_stop(connection, data)
        else:
            return None, None, int(GatewayCloseCode.UNKNOWN_OPCODE)

    async def _handle_heartbeat(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle heartbeat opcode."""
        connection.record_heartbeat()
        connection.record_heartbeat_ack()
        return int(GatewayOpcode.HEARTBEAT_ACK), None, None

    async def _handle_identify(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle identify opcode."""
        if connection.state == ConnectionState.READY:
            return None, None, int(GatewayCloseCode.ALREADY_AUTHENTICATED)

        if not data:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        token = data.get("token")
        if not token:
            return None, None, int(GatewayCloseCode.AUTHENTICATION_FAILED)

        intents = data.get("intents", DEFAULT_INTENTS)
        if not validate_intents(intents):
            return None, None, int(GatewayCloseCode.INVALID_INTENTS)

        properties = data.get("properties", {})
        connection.properties = properties

        user_agent = properties.get("browser") or properties.get("$browser")
        if not user_agent:
            # Fallback to handshake header
            user_agent = connection.websocket.headers.get("User-Agent")

        user_id = await self._verify_token(
            token,
            ip_address=connection.websocket.client.host
            if connection.websocket.client
            else None,
            user_agent=user_agent,
            is_selftest=connection.is_selftest,
        )
        if user_id is None:
            return None, None, int(GatewayCloseCode.AUTHENTICATION_FAILED)

        compress = data.get("compress", False)
        if compress:
            connection.enable_compression()

        session = self._session_manager.create_session(connection, user_id, intents)

        ready_data = await self._build_ready_payload(user_id, session.session_id)

        logger.info(f"User {user_id} identified with session {session.session_id}")

        # Dispatch online presence to friends
        await self._dispatch_online_presence(user_id)

        return (
            int(GatewayOpcode.DISPATCH),
            {
                "t": "READY",
                "s": connection.increment_sequence(),
                "d": ready_data,
            },
            None,
        )

    async def _handle_resume(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle resume opcode."""
        if not data:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        token = data.get("token")
        session_id = data.get("session_id")
        seq = data.get("seq", 0)

        if not token or not session_id:
            return int(GatewayOpcode.INVALID_SESSION), {"d": False}, None

        user_id = await self._verify_token(token, is_selftest=connection.is_selftest)
        if user_id is None:
            return int(GatewayOpcode.INVALID_SESSION), {"d": False}, None

        if not self._session_manager.can_resume_session(session_id, user_id):
            return int(GatewayOpcode.INVALID_SESSION), {"d": False}, None

        session = self._session_manager.resume_session(connection, session_id, seq)
        if not session:
            return int(GatewayOpcode.INVALID_SESSION), {"d": False}, None

        logger.info(f"User {user_id} resumed session {session_id}")

        return (
            int(GatewayOpcode.DISPATCH),
            {
                "t": "RESUMED",
                "s": connection.sequence,
                "d": {},
            },
            None,
        )

    async def _handle_presence_update(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle presence update opcode."""
        if not connection.is_authenticated:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        if not data or not self._presence:
            return None, None, None

        status = data.get("status", "online")
        custom_status = data.get("custom_status")
        custom_emoji = data.get("custom_emoji")
        activities = data.get("activities", [])

        try:
            from src.core.presence import UserStatus

            status_enum = UserStatus(status)
            # connection.user_id is guaranteed non-None by is_authenticated check above
            assert connection.user_id is not None
            await run_in_threadpool(
                self._presence.set_status, connection.user_id, status_enum
            )

            if activities:
                activity = activities[0]
                from src.core.presence import ActivityType

                activity_type = ActivityType(activity.get("type", "custom"))
                await run_in_threadpool(
                    self._presence.set_activity,
                    connection.user_id,
                    activity_type,
                    activity.get("name", ""),
                    details=activity.get("details"),
                    state=activity.get("state"),
                )

            # Dispatch presence update to friends
            assert connection.user_id is not None
            await self._dispatch_presence_to_friends(
                connection.user_id,
                status if status != "invisible" else "offline",
                custom_status,
                custom_emoji,
            )
        except Exception as e:
            logger.warning(f"Presence update failed: {e}")

        return None, None, None

    async def _handle_voice_state_update(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle voice state update opcode."""
        if not connection.is_authenticated:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        if not data:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        channel_id = data.get("channel_id")
        self_mute = data.get("self_mute", False)
        self_deaf = data.get("self_deaf", False)
        self_video = data.get("self_video", False)
        self_stream = data.get("self_stream", False)

        if channel_id is None:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        # Convert channel_id to int if it's a string
        try:
            channel_id = int(channel_id)
        except (ValueError, TypeError):
            logger.warning(
                f"Invalid channel_id type in voice state update: {type(channel_id)}"
            )
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        if not connection.user_id:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        try:
            # Update voice state
            from src.core import voice

            voice_state = voice.update_voice_state(
                user_id=connection.user_id,
                self_mute=self_mute,
                self_deaf=self_deaf,
                streaming=self_stream,
                video=self_video,
            )

            # Broadcast voice state update to channel members via events
            try:
                await self._dispatch_voice_state_update(
                    user_id=connection.user_id,
                    channel_id_for_recipients=channel_id,
                    event_channel_id=channel_id,
                    voice_state=voice_state,
                )
            except Exception as broadcast_error:
                logger.warning(
                    f"Failed to broadcast voice state update: {broadcast_error}"
                )
                # Don't fail the update for broadcast issues

        except Exception as e:
            logger.warning(f"Voice state update failed: {e}")
            return None, None, None

        return None, None, None

    async def _handle_voice_connect(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle voice connect opcode."""
        if not connection.is_authenticated:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        if not data:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        channel_id = data.get("channel_id")
        if not channel_id:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        # Convert channel_id to int if it's a string
        try:
            channel_id = int(channel_id)
        except (ValueError, TypeError):
            logger.warning(f"Invalid channel_id type: {type(channel_id)}")
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        try:
            if not connection.user_id:
                return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

            # Import required modules
            from src.core import voice
            from src.core.voice import signaling

            # Step 1: Join voice channel (create/update persisted voice state)
            try:
                voice_state = voice.join_channel(connection.user_id, channel_id)
            except Exception as voice_error:
                logger.warning(f"Failed to join voice channel: {voice_error}")
                return None, None, None

            # Step 2: Create signaling connection
            try:
                info = signaling.create_voice_connection(connection.user_id, channel_id)
            except Exception as signaling_error:
                # Rollback: leave the voice channel if signaling fails
                try:
                    voice.leave_channel(connection.user_id)
                except Exception as rollback_error:
                    logger.warning(
                        f"Failed to rollback voice channel join: {rollback_error}"
                    )

                logger.warning(f"Voice signaling failed: {signaling_error}")
                return None, None, None

            # Step 3: Broadcast voice state update to channel members via events
            try:
                # Broadcast voice state update to channel members via events
                await self._dispatch_voice_state_update(
                    user_id=connection.user_id,
                    channel_id_for_recipients=channel_id,
                    event_channel_id=channel_id,
                    voice_state=voice_state,
                )
            except Exception as broadcast_error:
                logger.warning(
                    f"Failed to broadcast voice state update: {broadcast_error}"
                )
                # Don't fail the connection for broadcast issues

            # Include current peer list so clients can immediately negotiate mesh.
            peers: List[str] = []
            try:
                states = voice.get_channel_users(channel_id)
                peers = [
                    str(s.user_id)
                    for s in states
                    if int(s.user_id) != connection.user_id
                ]
            except Exception:
                peers = []

            info_dict = info.to_dict()
            info_dict["peers"] = peers

            return (
                int(GatewayOpcode.DISPATCH),
                {
                    "t": "VOICE_SERVER_UPDATE",
                    "s": connection.increment_sequence(),
                    "d": info_dict,
                },
                None,
            )
        except Exception as e:
            logger.warning(f"Voice connect failed: {e}")
            return None, None, None

    async def _handle_voice_disconnect(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle voice disconnect opcode."""
        if not connection.is_authenticated:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        assert connection.user_id is not None  # Guaranteed by is_authenticated
        channel_id: Optional[Any] = data.get("channel_id") if data else None

        try:
            from src.core import voice
            from src.core.voice import signaling

            prev_state = voice.get_voice_state(connection.user_id)
            prev_channel_id = (
                getattr(prev_state, "channel_id", None) if prev_state else None
            )

            # Disconnect signaling (TURN creds, SFU cleanup if enabled)
            signaling.disconnect_voice(connection.user_id, channel_id)

            # Leave voice channel (remove persisted state)
            if prev_state:
                voice.leave_channel(connection.user_id)

            # Broadcast "left" update to previous channel recipients.
            if prev_channel_id:
                await self._dispatch_voice_state_update(
                    user_id=connection.user_id,
                    channel_id_for_recipients=int(prev_channel_id),
                    event_channel_id=None,
                    voice_state=prev_state,
                )
        except Exception as e:
            logger.warning(f"Voice disconnect failed: {e}")

        return None, None, None

    async def _handle_voice_sdp_offer(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle voice SDP offer opcode."""
        if not connection.is_authenticated:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        if not data:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        channel_id = data.get("channel_id")
        sdp = data.get("sdp")
        sdp_type = data.get("type", "offer")

        target_user_id = data.get("target_user_id") or data.get("targetUserId")

        if not channel_id or not sdp or not target_user_id:
            logger.warning(
                f"SDP offer missing required fields: channel_id={channel_id}, sdp_present={bool(sdp)}, target_user_id={target_user_id}"
            )
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        # Convert channel_id to int if it's a string
        try:
            channel_id = int(channel_id)
        except (ValueError, TypeError):
            logger.warning(f"Invalid channel_id type in SDP offer: {type(channel_id)}")
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        try:
            target_user_id = int(target_user_id)
        except (ValueError, TypeError):
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        if not connection.user_id:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        try:
            # Mesh signaling: route offer to the target peer (same channel only).
            from src.api.websocket import get_dispatcher, get_session_manager
            from src.core import voice

            sender_id = int(connection.user_id)
            sender_state = voice.get_voice_state(sender_id)
            target_state = voice.get_voice_state(target_user_id)
            if (
                not sender_state
                or not target_state
                or int(getattr(sender_state, "channel_id", 0) or 0) != channel_id
                or int(getattr(target_state, "channel_id", 0) or 0) != channel_id
            ):
                return None, None, None

            session_manager = get_session_manager()
            dispatcher = get_dispatcher()
            target_conns = session_manager.get_connections_for_users([target_user_id])
            if not target_conns:
                return None, None, None

            payload = {
                "channel_id": str(channel_id),
                "from_user_id": str(sender_id),
                "target_user_id": str(target_user_id),
                "type": sdp_type,
                "sdp": sdp,
            }

            for conn in target_conns:
                await dispatcher.dispatch_raw(
                    conn, GatewayOpcode.VOICE_SDP_OFFER, payload
                )

            return None, None, None
        except Exception as e:
            logger.warning(f"SDP offer handling failed: {e}")
            return None, None, None

    async def _handle_voice_ice_candidate(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle voice ICE candidate opcode."""
        if not connection.is_authenticated:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        if not data:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        channel_id = data.get("channel_id")
        candidate = data.get("candidate")
        target_user_id = data.get("target_user_id") or data.get("targetUserId")
        # Support both camelCase (from JS) and snake_case field names
        sdp_mid = data.get("sdp_mid") or data.get("sdpMid")
        sdp_mline_index = data.get("sdp_mline_index") or data.get("sdpMLineIndex")

        if not channel_id or not candidate or not target_user_id:
            logger.warning(
                f"ICE candidate missing required fields: channel_id={channel_id}, candidate={candidate}, data={data}"
            )
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        # Convert channel_id to int if it's a string
        try:
            channel_id = int(channel_id)
        except (ValueError, TypeError):
            logger.warning(
                f"Invalid channel_id type in ICE candidate: {type(channel_id)}"
            )
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        try:
            target_user_id = int(target_user_id)
        except (ValueError, TypeError):
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        if not connection.user_id:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        try:
            # Mesh signaling: route ICE candidate to the target peer (same channel only).
            from src.api.websocket import get_dispatcher, get_session_manager
            from src.core import voice

            sender_id = int(connection.user_id)
            sender_state = voice.get_voice_state(sender_id)
            target_state = voice.get_voice_state(target_user_id)
            if (
                not sender_state
                or not target_state
                or int(getattr(sender_state, "channel_id", 0) or 0) != channel_id
                or int(getattr(target_state, "channel_id", 0) or 0) != channel_id
            ):
                return None, None, None

            session_manager = get_session_manager()
            dispatcher = get_dispatcher()
            target_conns = session_manager.get_connections_for_users([target_user_id])
            if not target_conns:
                return None, None, None

            payload = {
                "channel_id": str(channel_id),
                "from_user_id": str(sender_id),
                "target_user_id": str(target_user_id),
                "candidate": candidate,
                "sdp_mid": sdp_mid,
                "sdp_mline_index": sdp_mline_index,
            }

            for conn in target_conns:
                await dispatcher.dispatch_raw(
                    conn, GatewayOpcode.VOICE_ICE_CANDIDATE, payload
                )
        except Exception as e:
            logger.warning(f"ICE candidate handling failed: {e}")

        return None, None, None

    async def _handle_voice_sdp_answer(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle voice SDP answer opcode (mesh relay)."""
        if not connection.is_authenticated:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        if not data:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        channel_id = data.get("channel_id")
        sdp = data.get("sdp")
        sdp_type = data.get("type", "answer")
        target_user_id = data.get("target_user_id") or data.get("targetUserId")

        if not channel_id or not sdp or not target_user_id:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        try:
            channel_id = int(channel_id)
            target_user_id = int(target_user_id)
        except (ValueError, TypeError):
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        if not connection.user_id:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        try:
            from src.api.websocket import get_dispatcher, get_session_manager
            from src.core import voice

            sender_id = int(connection.user_id)
            sender_state = voice.get_voice_state(sender_id)
            target_state = voice.get_voice_state(target_user_id)
            if (
                not sender_state
                or not target_state
                or int(getattr(sender_state, "channel_id", 0) or 0) != channel_id
                or int(getattr(target_state, "channel_id", 0) or 0) != channel_id
            ):
                return None, None, None

            session_manager = get_session_manager()
            dispatcher = get_dispatcher()
            target_conns = session_manager.get_connections_for_users([target_user_id])
            if not target_conns:
                return None, None, None

            payload = {
                "channel_id": str(channel_id),
                "from_user_id": str(sender_id),
                "target_user_id": str(target_user_id),
                "type": sdp_type,
                "sdp": sdp,
            }

            for conn in target_conns:
                await dispatcher.dispatch_raw(
                    conn, GatewayOpcode.VOICE_SDP_ANSWER, payload
                )
        except Exception as e:
            logger.warning(f"SDP answer relay failed: {e}")

        return None, None, None

    async def _handle_voice_speaking(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle voice speaking opcode."""
        if not connection.is_authenticated:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        if not data:
            return None, None, None

        channel_id = data.get("channel_id")
        speaking = data.get("speaking")
        if channel_id is None:
            return None, None, None

        try:
            channel_id = int(channel_id)
        except (ValueError, TypeError):
            return None, None, None

        if not connection.user_id:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        try:
            from src.api.websocket import get_dispatcher, get_session_manager
            from src.core import voice

            sender_id = int(connection.user_id)
            sender_state = voice.get_voice_state(sender_id)
            if (
                not sender_state
                or int(getattr(sender_state, "channel_id", 0) or 0) != channel_id
            ):
                return None, None, None

            states = voice.get_channel_users(channel_id)
            user_ids = [int(s.user_id) for s in states if int(s.user_id) != sender_id]
            if not user_ids:
                return None, None, None

            session_manager = get_session_manager()
            dispatcher = get_dispatcher()
            conns = session_manager.get_connections_for_users(user_ids)

            payload = {
                "channel_id": str(channel_id),
                "user_id": str(sender_id),
                "speaking": bool(speaking),
            }
            for conn in conns:
                await dispatcher.dispatch_raw(
                    conn, GatewayOpcode.VOICE_SPEAKING, payload
                )
        except Exception as e:
            logger.warning(f"Speaking relay failed: {e}")

        return None, None, None

    async def _handle_voice_quality(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle voice quality opcode."""
        if not connection.is_authenticated:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        assert connection.user_id is not None  # Guaranteed by is_authenticated

        if not data:
            return None, None, None

        channel_id = data.get("channel_id")
        target_bitrate = data.get("target_bitrate")
        quality_level = data.get("quality_level")

        if channel_id:
            try:
                from src.core.voice import signaling

                signaling.update_quality_hint(
                    connection.user_id, channel_id, target_bitrate, quality_level
                )
            except Exception as e:
                logger.warning(f"Quality update failed: {e}")

        # Broadcast quality state to other channel members (mesh informational).
        try:
            if channel_id and connection.user_id is not None:
                from src.api.websocket import get_dispatcher, get_session_manager
                from src.core import voice

                channel_id_int = int(channel_id)
                sender_id = int(connection.user_id)
                sender_state = voice.get_voice_state(sender_id)
                if (
                    sender_state
                    and int(getattr(sender_state, "channel_id", 0) or 0)
                    == channel_id_int
                ):
                    states = voice.get_channel_users(channel_id_int)
                    user_ids = [
                        int(s.user_id) for s in states if int(s.user_id) != sender_id
                    ]
                    if user_ids:
                        session_manager = get_session_manager()
                        dispatcher = get_dispatcher()
                        conns = session_manager.get_connections_for_users(user_ids)
                        payload = {
                            "channel_id": str(channel_id_int),
                            "user_id": str(sender_id),
                            "target_bitrate": target_bitrate,
                            "quality_level": quality_level,
                        }
                        for conn in conns:
                            await dispatcher.dispatch_raw(
                                conn, GatewayOpcode.VOICE_QUALITY, payload
                            )
        except Exception as e:
            logger.warning(f"Quality relay failed: {e}")

        return None, None, None

    async def _handle_request_guild_members(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle request guild members opcode."""
        if not connection.is_authenticated:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        if not data or "guild_id" not in data:
            return None, None, None

        try:
            guild_id = int(data["guild_id"])
        except (ValueError, TypeError):
            return None, None, None

        if not self._servers or not self._auth or not self._presence:
            return None, None, None

        user_id = connection.user_id
        assert user_id is not None

        # 1. Verify user is a member of the guild
        try:
            member = await run_in_threadpool(
                self._servers.get_member, guild_id, user_id
            )
            if not member:
                return None, None, None
        except Exception:
            return None, None, None

        # 2. Get members (for now, just get first 1000)
        try:
            members = await run_in_threadpool(
                self._servers.get_members, user_id, guild_id, limit=1000
            )
            if not members:
                return None, None, None

            user_ids = [m.user_id for m in members]

            # Bulk fetch user data
            users_map = await run_in_threadpool(self._auth.get_users_bulk, user_ids)

            # Bulk fetch presence data
            presence_map = {}
            try:
                presence_map = await run_in_threadpool(
                    self._presence.get_visible_presences_bulk, user_id, user_ids
                )
            except Exception as e:
                logger.warning(
                    f"WS: Failed to get bulk presence for server {guild_id}: {e}"
                )

            member_list = []
            for m in members:
                u_id = m.user_id
                user = users_map.get(u_id)

                pres = presence_map.get(u_id)
                status = "offline"
                if pres:
                    status_obj = getattr(pres, "status", None)
                    status = (
                        str(
                            status_obj.value
                            if hasattr(status_obj, "value")
                            else status_obj
                        )
                        if status_obj
                        else "offline"
                    )

                member_list.append(
                    {
                        "user_id": str(u_id),
                        "username": user.username if user else f"User {u_id}",
                        "nickname": m.nickname,
                        "avatar_url": getattr(user, "avatar_url", None)
                        or getattr(m, "avatar_url", None),
                        "joined_at": m.joined_at,
                        "roles": [str(r) for r in (m.roles or [])],
                        "presence": {"status": status},
                    }
                )

            # 3. Dispatch chunk
            from src.api.websocket import get_dispatcher

            dispatcher = get_dispatcher()

            event = events_mod.create_guild_members_chunk(
                server_id=guild_id, members=member_list, chunk_index=0, chunk_count=1
            )

            await dispatcher.dispatch_to_connection(connection, event)

        except Exception as e:
            logger.error(
                f"WS: Failed to process guild members request for {guild_id}: {e}",
                exc_info=True,
            )

        return None, None, None

    async def _handle_typing_start(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle typing start opcode - user started typing in a channel."""
        if not connection.is_authenticated:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        if not data:
            return None, None, None

        channel_id = data.get("channel_id")
        if not channel_id:
            return None, None, None

        try:
            channel_id = int(channel_id)
        except (ValueError, TypeError):
            return None, None, None

        assert connection.user_id is not None

        # Get username for the typing event
        username = None
        if self._auth:
            try:
                user = await run_in_threadpool(self._auth.get_user, connection.user_id)
                if user:
                    username = user.username
            except Exception:
                pass

        # Record typing in presence module
        if self._presence:
            try:
                await run_in_threadpool(
                    self._presence.start_typing, connection.user_id, channel_id
                )
            except Exception:
                pass

        # Dispatch typing event to channel members
        await self._dispatch_typing_event(
            connection.user_id, channel_id, username, is_start=True
        )

        return None, None, None

    async def _handle_typing_stop(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle typing stop opcode - user stopped typing in a channel."""
        if not connection.is_authenticated:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        if not data:
            return None, None, None

        channel_id = data.get("channel_id")
        if not channel_id:
            return None, None, None

        try:
            channel_id = int(channel_id)
        except (ValueError, TypeError):
            return None, None, None

        assert connection.user_id is not None

        # Clear typing in presence module
        if self._presence:
            try:
                await run_in_threadpool(
                    self._presence.stop_typing, connection.user_id, channel_id
                )
            except Exception:
                pass

        # Dispatch typing stop event to channel members
        await self._dispatch_typing_event(
            connection.user_id, channel_id, None, is_start=False
        )

        return None, None, None

    async def _dispatch_typing_event(
        self,
        user_id: int,
        channel_id: int,
        username: Optional[str],
        is_start: bool,
    ) -> None:
        """Dispatch typing start/stop event to channel members."""
        try:
            import src.api as api
            from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
            from src.core.events.models import Event
            from src.core.events.types import EventType

            if not ws_is_setup():
                return

            dispatcher = get_dispatcher()
            user_ids: List[int] = []
            server_id: Optional[int] = None

            # Try to get channel members from servers module
            if self._servers:
                try:
                    channel = await run_in_threadpool(
                        self._servers.get_channel, channel_id, user_id
                    )
                    if channel:
                        server_id = getattr(channel, "server_id", None)
                        if server_id:
                            user_ids = await run_in_threadpool(
                                self._get_typing_recipient_ids,
                                user_id,
                                channel_id,
                                server_id,
                            )
                except Exception:
                    pass

            # If not a server channel, try DM
            if not user_ids:
                messaging = api.get_messaging()
                if messaging:
                    try:
                        participants = await run_in_threadpool(
                            messaging.get_participants, user_id, channel_id
                        )
                        if participants:
                            user_ids = [
                                p.user_id for p in participants if p.user_id != user_id
                            ]
                    except Exception:
                        pass

            if not user_ids:
                return

            if is_start:
                event = Event(
                    event_type=EventType.TYPING_START,
                    data={
                        "channel_id": str(channel_id),
                        "user_id": str(user_id),
                        "username": username or "Someone",
                    },
                    server_id=server_id,
                    channel_id=channel_id,
                )
            else:
                event = Event(
                    event_type=EventType.TYPING_STOP,
                    data={
                        "channel_id": str(channel_id),
                        "user_id": str(user_id),
                    },
                    server_id=server_id,
                    channel_id=channel_id,
                )

            await dispatcher.dispatch_event(event, user_ids)
        except Exception as e:
            logger.debug(f"Failed to dispatch typing event: {e}")

    def _get_typing_recipient_ids(
        self, user_id: int, channel_id: int, server_id: int, include_self: bool = False
    ) -> List[int]:
        """Return only members who can still view a channel."""
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

    async def _verify_token(
        self,
        token: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        is_selftest: bool = False,
    ) -> Optional[int]:
        """Verify a token and return user ID."""
        if not self._auth:
            return None

        try:
            token_info: TokenInfo = await run_in_threadpool(
                self._auth.verify_token,
                token,
                ip_address,
                user_agent,
                is_selftest=is_selftest,
            )
            return token_info.user_id
        except Exception:
            return None

    async def _build_ready_payload(
        self,
        user_id: int,
        session_id: str,
    ) -> Dict[str, Any]:
        """Build the READY event payload."""
        user_data = {"id": str(user_id)}
        guilds = []

        if self._auth:
            try:
                user = await run_in_threadpool(self._auth.get_user, user_id)
                if user:
                    user_data = {
                        "id": str(user_id),
                        "username": user.username,
                        "discriminator": "0",
                        "avatar": None,
                        "bot": False,
                    }
            except Exception:
                pass

        if self._servers:
            try:
                servers = await run_in_threadpool(self._servers.get_servers, user_id)
                for server in servers or []:
                    guilds.append(
                        {
                            "id": str(server.id),
                            "name": server.name,
                            "unavailable": False,
                        }
                    )
            except Exception:
                pass

        return {
            "v": 10,
            "user": user_data,
            "guilds": guilds,
            "session_id": session_id,
            "resume_gateway_url": "wss://gateway.example.com",
            "application": {"id": str(user_id), "flags": 0},
        }

    async def _dispatch_online_presence(self, user_id: int) -> None:
        """Dispatch online presence to friends when user connects."""
        # Set presence to online FIRST before dispatching
        if self._presence:
            try:
                from src.core.presence.models import UserStatus

                await run_in_threadpool(
                    self._presence.set_status, user_id, UserStatus.ONLINE
                )
                logger.debug(f"Set user {user_id} presence to ONLINE on connect")
            except Exception as e:
                logger.warning(f"Failed to set online presence for user {user_id}: {e}")

        # Then dispatch to friends
        await self._dispatch_presence_to_friends(user_id, "online", None, None)

    async def _dispatch_presence_to_friends(
        self,
        user_id: int,
        status: str,
        custom_status: Optional[str] = None,
        custom_emoji: Optional[str] = None,
    ) -> None:
        """Dispatch presence update to friends and server members."""
        try:
            from src.api.routes.presence import _get_presence_targets

            # Use the robust, cached target selection logic from the presence route
            target_user_ids = _get_presence_targets(user_id)

            if not target_user_ids:
                return

            # Dispatch presence update
            from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
            from src.core.events.models import Event
            from src.core.events.types import EventType

            if ws_is_setup():
                dispatcher = get_dispatcher()
                event = Event(
                    event_type=EventType.PRESENCE_UPDATE,
                    data={
                        "user_id": str(user_id),
                        "status": status,
                        "custom_status": custom_status,
                        "custom_emoji": custom_emoji,
                    },
                )
                await dispatcher.dispatch_event(event, list(target_user_ids))
                logger.debug(
                    f"Dispatched presence update for user {user_id} to {len(target_user_ids)} users"
                )
        except Exception as e:
            logger.debug(f"Failed to dispatch presence: {e}")

    async def _dispatch_voice_state_update(
        self,
        user_id: int,
        channel_id_for_recipients: int,
        event_channel_id: Optional[int],
        voice_state: Any,
    ) -> None:
        """Dispatch voice state update to channel members."""
        try:
            from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
            from src.core.events import payloads

            if not ws_is_setup():
                return

            dispatcher = get_dispatcher()

            # Get server_id if it's a server channel
            server_id = None
            if self._servers:
                try:
                    channel = await run_in_threadpool(
                        self._servers.get_channel, channel_id_for_recipients, user_id
                    )
                    if channel:
                        server_id = getattr(channel, "server_id", None)
                except Exception:
                    pass

            # Create the event
            event = payloads.create_voice_state_update(
                user_id=user_id,
                channel_id=event_channel_id,
                server_id=server_id,
                self_mute=voice_state.self_mute,
                self_deaf=voice_state.self_deaf,
                mute=getattr(voice_state, "server_mute", False),
                deaf=getattr(voice_state, "server_deaf", False),
                session_id=None,  # Not tracked here yet
            )

            # Find recipients (everyone in the server who can see the channel)
            target_user_ids: List[int] = []
            if server_id:
                target_user_ids = await run_in_threadpool(
                    self._get_typing_recipient_ids,
                    user_id,
                    channel_id_for_recipients,
                    server_id,
                    True,  # include_self for voice states
                )
            else:
                # Handle group DM or DM later if needed
                pass

            if target_user_ids:
                await dispatcher.dispatch_event(event, target_user_ids)
                logger.debug(
                    f"Dispatched voice state update for user {user_id} to {len(target_user_ids)} users"
                )

        except Exception as e:
            logger.debug(f"Failed to dispatch voice state update: {e}")
