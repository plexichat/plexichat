"""
Connection handlers - Handle connection-related opcodes (heartbeat, identify, resume).
"""

from typing import Optional, Dict, Any, Tuple, TYPE_CHECKING

import utils.logger as logger
from starlette.concurrency import run_in_threadpool

from src.api.websocket.opcodes import GatewayOpcode, GatewayCloseCode
from src.api.websocket.connection import Connection, ConnectionState
from src.api.websocket.session import SessionManager
from src.api.websocket.intents import DEFAULT_INTENTS
from src.core.auth.models import TokenInfo

if TYPE_CHECKING:
    from src.core.auth.manager import AuthManager
    from src.core.presence.manager import PresenceManager
    from src.core.servers.manager import ServerManager


class ConnectionHandler:
    """Handles connection-related opcodes."""

    def __init__(
        self,
        session_manager: SessionManager,
        auth_module: Optional["AuthManager"] = None,
        presence_module: Optional["PresenceManager"] = None,
        servers_module: Optional["ServerManager"] = None,
    ):
        """
        Initialize the connection handler.

        Args:
            session_manager: Session manager instance
            auth_module: Auth module for token verification
            presence_module: Presence module for status updates
            servers_module: Servers module used to resolve guilds for the
                READY payload. Injected so the handler never has to reach
                into the process-global ``src.api`` state.
        """
        self._session_manager = session_manager
        self._auth: Optional["AuthManager"] = auth_module
        self._presence: Optional["PresenceManager"] = presence_module
        self._servers: Optional["ServerManager"] = servers_module

    async def handle_heartbeat(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle heartbeat opcode.

        Only the RECEIPT is recorded here. The ACK timestamp is recorded by
        the sender *after* the HEARTBEAT_ACK frame is actually written (see
        ``gateway`` / ``GatewayDispatcher.send_heartbeat_ack``), so
        ``latency_ms`` measures a real receive->send interval instead of the
        near-zero gap between two back-to-back calls (Agent 38 F7).
        """
        connection.record_heartbeat()

        # CORRECTNESS FIX: keep last_seen fresh while the user is connected.
        # Without this, last_seen stays frozen at the moment they connected
        # (or went offline), so "last seen" reported stale data for active
        # users.
        if self._presence and connection.is_authenticated and connection.user_id:
            try:
                await run_in_threadpool(
                    self._presence.update_last_seen, connection.user_id
                )
            except Exception as e:
                logger.debug(f"Heartbeat last_seen update failed: {e}")

        return int(GatewayOpcode.HEARTBEAT_ACK), None, None

    async def handle_identify(
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
        if not self._validate_intents(intents):
            return None, None, int(GatewayCloseCode.INVALID_INTENTS)

        properties = data.get("properties", {})
        connection.properties = properties

        user_agent = properties.get("browser") or properties.get("$browser")
        if not user_agent:
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

        # Enforce the per-user connection cap before creating a session. A
        # user flooding new IDENTIFY frames from additional tabs must be
        # rejected with RATE_LIMITED instead of silently exceeding the limit.
        if not self._session_manager.can_user_connect(user_id):
            logger.warning(
                f"User {user_id} exceeded max connections; identify rejected"
            )
            return None, None, int(GatewayCloseCode.RATE_LIMITED)

        compress = data.get("compress", False)
        if compress:
            connection.enable_compression()

        session = self._session_manager.create_session(connection, user_id, intents)

        ready_data = await self._build_ready_payload(user_id, session.session_id)

        logger.info(f"User {user_id} identified with session {session.session_id}")

        # Dispatch online presence to friends
        await self._dispatch_online_presence(user_id)

        ready_payload = {
            "op": int(GatewayOpcode.DISPATCH),
            "t": "READY",
            "s": connection.increment_sequence(),
            "d": ready_data,
        }
        # Persist the initial gateway sequence and replay frame before this
        # worker can be replaced. Without this, a sibling worker could hydrate
        # sequence 0 while the client has already acknowledged READY at 1.
        self._session_manager.record_event(session.session_id, ready_payload)

        return (
            int(GatewayOpcode.DISPATCH),
            {
                "t": ready_payload["t"],
                "s": ready_payload["s"],
                "d": ready_payload["d"],
            },
            None,
        )

    async def handle_resume(
        self,
        connection: Connection,
        data: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], Optional[Dict[str, Any]], Optional[int]]:
        """Handle resume opcode."""
        if data is None:
            return None, None, int(GatewayCloseCode.DECODE_ERROR)

        token = data.get("token")
        session_id = data.get("session_id")
        raw_seq = data.get("seq", 0)

        if not token or not session_id:
            return int(GatewayOpcode.INVALID_SESSION), {"d": False}, None
        if isinstance(raw_seq, bool) or not isinstance(raw_seq, int):
            return int(GatewayOpcode.INVALID_SESSION), {"d": False}, None
        seq = raw_seq
        if seq < 0:
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

    def _validate_intents(self, intents: int) -> bool:
        """Validate intents value."""
        from src.api.websocket.intents import validate_intents

        return validate_intents(intents)

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
        user_data: Dict[str, Any] = {"id": str(user_id)}
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

        # P1 parity: the client reads data.user.presence.status on boot to
        # honour the server-known status (including a persisted idle/dnd).
        # Without this object every reconnect silently reset to "online".
        presence_status = "online"
        if self._presence:
            try:
                status_enum = await run_in_threadpool(
                    self._presence.get_status, user_id
                )
                presence_status = (
                    status_enum.value
                    if hasattr(status_enum, "value")
                    else str(status_enum)
                )
            except Exception:
                pass
        user_data["presence"] = {"status": presence_status}

        # Resolve guilds through the INJECTED servers boundary only, so the
        # handler stays fully self-contained (and unit tests never touch a
        # live database). Reaching into the process-global ``src.api`` here
        # used to fire a real ``get_servers`` query from an AnyIO worker
        # thread on every identify; each distinct worker thread then held a
        # SQLite pool connection in thread-local state forever, exhausting
        # the shared pool (max 16) and stalling later identifies for the
        # full connect timeout. The gateway always injects the servers
        # module, so a handler without one simply reports no guilds.
        servers_mod = self._servers
        if servers_mod:
            try:
                servers = await run_in_threadpool(servers_mod.get_servers, user_id)
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
        """Dispatch online presence to friends when user connects.

        CORRECTNESS FIX: a persisted idle/dnd status chosen by the user via
        REST must survive reconnects. Only an OFFLINE user is promoted to
        ONLINE; invisible users stay scrubbed to offline; the broadcast
        reflects the persisted custom status instead of wiping it with None.
        """
        # Set presence FIRST before dispatching
        if self._presence:
            from src.core.presence.models import UserStatus

            try:
                current_status = await run_in_threadpool(
                    self._presence.get_status, user_id
                )
            except Exception:
                current_status = UserStatus.ONLINE

            if current_status == UserStatus.INVISIBLE:
                # An invisible user must stay invisible; dispatch as offline
                await self._dispatch_presence_to_friends(user_id, "offline", None, None)
                return

            if current_status == UserStatus.OFFLINE:
                try:
                    await run_in_threadpool(
                        self._presence.set_status, user_id, UserStatus.ONLINE
                    )
                    current_status = UserStatus.ONLINE
                    logger.debug(f"Set user {user_id} presence to ONLINE on connect")
                except Exception as e:
                    logger.warning(
                        f"Failed to set online presence for user {user_id}: {e}"
                    )

            # Reflect the persisted custom status (text and/or emoji-only) so
            # friends' clients do not see it flash to null on reconnect.
            custom_text: Optional[str] = None
            custom_emoji: Optional[str] = None
            try:
                pres = await run_in_threadpool(self._presence.get_presence, user_id)
                if pres and pres.custom_status:
                    custom_text = pres.custom_status.text
                    custom_emoji = pres.custom_status.emoji
            except Exception as e:
                logger.debug(f"Failed to read custom status for {user_id}: {e}")

            current_str = (
                current_status.value
                if hasattr(current_status, "value")
                else str(current_status)
            )
            # Then dispatch to friends with the ACTUAL persisted state.
            await self._dispatch_presence_to_friends(
                user_id, current_str, custom_text, custom_emoji
            )
        else:
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
