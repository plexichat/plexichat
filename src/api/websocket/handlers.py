"""
Opcode handlers - Handle incoming gateway messages.
"""

from typing import Optional, Dict, Any, Tuple, List, TYPE_CHECKING

import utils.logger as logger

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

        user_id = await self._verify_token(
            token,
            ip_address=connection.websocket.client.host
            if connection.websocket.client
            else None,
            user_agent=connection.properties.get("browser"),
            is_selftest=connection.is_selftest,
        )
        if user_id is None:
            return None, None, int(GatewayCloseCode.AUTHENTICATION_FAILED)

        # Bypass rate limits for self-test
        if not connection.is_selftest and not self._session_manager.can_user_connect(
            user_id
        ):
            return None, None, int(GatewayCloseCode.RATE_LIMITED)

        properties = data.get("properties", {})
        connection.properties = properties

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
            self._presence.set_status(connection.user_id, status_enum)

            if activities:
                activity = activities[0]
                from src.core.presence import ActivityType

                activity_type = ActivityType(activity.get("type", "custom"))
                self._presence.set_activity(
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
            from src.core.voice import signaling

            info = signaling.create_voice_connection(connection.user_id, channel_id)
            return (
                int(GatewayOpcode.DISPATCH),
                {
                    "t": "VOICE_SERVER_UPDATE",
                    "s": connection.increment_sequence(),
                    "d": info.to_dict(),
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
            from src.core.voice import signaling

            signaling.disconnect_voice(connection.user_id, channel_id)
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

        if not channel_id or not sdp:
            logger.warning(
                f"SDP offer missing required fields: channel_id={channel_id}, sdp_present={bool(sdp)}, data keys={data.keys() if data else None}"
            )
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        # Convert channel_id to int if it's a string
        try:
            channel_id = int(channel_id)
        except (ValueError, TypeError):
            logger.warning(f"Invalid channel_id type in SDP offer: {type(channel_id)}")
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        if not connection.user_id:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        try:
            from src.core.voice import signaling

            # Use async version to actually communicate with SFU
            answer = await signaling.handle_sdp_offer_async(
                connection.user_id, channel_id, sdp, sdp_type
            )
            return int(GatewayOpcode.VOICE_SDP_ANSWER), answer.to_dict(), None
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
        # Support both camelCase (from JS) and snake_case field names
        sdp_mid = data.get("sdp_mid") or data.get("sdpMid")
        sdp_mline_index = data.get("sdp_mline_index") or data.get("sdpMLineIndex")

        if not channel_id or not candidate:
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

        if not connection.user_id:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        try:
            from src.core.voice import signaling

            signaling.handle_ice_candidate(
                connection.user_id, channel_id, candidate, sdp_mid, sdp_mline_index
            )
        except Exception as e:
            logger.warning(f"ICE candidate handling failed: {e}")

        return None, None, None

    async def _handle_voice_speaking(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle voice speaking opcode."""
        if not connection.is_authenticated:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

        # Speaking state is informational, broadcast to channel
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

        return None, None, None

    async def _handle_request_guild_members(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle request guild members opcode."""
        if not connection.is_authenticated:
            return None, None, int(GatewayCloseCode.NOT_AUTHENTICATED)

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
                user = self._auth.get_user(connection.user_id)
                if user:
                    username = user.username
            except Exception:
                pass

        # Record typing in presence module
        if self._presence:
            try:
                self._presence.start_typing(connection.user_id, channel_id)
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
                self._presence.stop_typing(connection.user_id, channel_id)
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
                    channel = self._servers.get_channel(channel_id, user_id)
                    if channel:
                        server_id = getattr(channel, "server_id", None)
                        if server_id:
                            user_ids = self._servers.get_member_user_ids(
                                server_id, exclude_user_id=user_id
                            )
                except Exception:
                    pass

            # If not a server channel, try DM
            if not user_ids:
                messaging = api.get_messaging()
                if messaging:
                    try:
                        participants = messaging.get_participants(user_id, channel_id)
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
            token_info: TokenInfo = self._auth.verify_token(
                token, ip_address, user_agent
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
                user = self._auth.get_user(user_id)
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
                servers = self._servers.get_servers(user_id)
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

    def get_replay_events(
        self,
        session_id: str,
        after_sequence: int,
    ) -> list:
        """Get events to replay after resume."""
        return self._session_manager.get_replay_events(session_id, after_sequence)

    async def _dispatch_online_presence(self, user_id: int) -> None:
        """Dispatch online presence to friends when user connects."""
        # Set presence to online FIRST before dispatching
        if self._presence:
            try:
                from src.core.presence.models import UserStatus

                self._presence.set_status(user_id, UserStatus.ONLINE)
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
            import src.api as api

            relationships = api.get_relationships()

            # Collect all user IDs who should receive this presence update
            target_user_ids = set()

            # Add friends
            if relationships:
                friend_ids = relationships.get_friend_ids(user_id)
                if friend_ids:
                    target_user_ids.update(friend_ids)

            # Add server members (users in shared servers) - Optimized single query
            if self._servers:
                try:
                    shared_member_ids = self._servers.get_all_shared_member_ids(user_id)
                    if shared_member_ids:
                        target_user_ids.update(shared_member_ids)
                except Exception as e:
                    logger.debug(
                        f"Failed to get shared server members for presence: {e}"
                    )

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
