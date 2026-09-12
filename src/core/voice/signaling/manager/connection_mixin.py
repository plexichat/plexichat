"""Connection management mixin for SignalingManager."""

import asyncio
import secrets
from typing import TYPE_CHECKING, List, Dict, Any, Optional

import utils.logger as logger

from ..exceptions import AlreadyConnectedError, NotConnectedError
from ..models import SignalingState, VoiceServerInfo, VoiceConnection


# Default lifetime for minted media-plane tokens (4 hours). Tokens issued
# with VoiceServerInfo must be re-obtained after expiry.
MEDIA_TOKEN_TTL_MS = 4 * 60 * 60 * 1000


class ConnectionManagementMixin:
    """Mixin handling connection lifecycle methods."""

    if TYPE_CHECKING:

        def _generate_session_id(self) -> str: ...
        def _get_timestamp(self) -> int: ...
        def _voice_manager(self) -> Optional[Any]: ...

    _voice: Optional[Any]
    _connections: Dict[int, VoiceConnection]
    _ice_manager: Any
    _sfu_config: Dict[str, Any]
    _ice_builder: Any
    _get_sfu: Any
    _media_tokens: Dict[str, Dict[str, Any]]

    def _require_connect_permission(self, user_id: int, channel_id: int) -> None:
        """Enforce the ``voice.connect`` permission for a channel.

        SECURITY (Agent 34 C3/M6): ``get_voice_channel`` only enforces
        ``channels.view``, so any code path that allocates media-plane
        resources (SFU rooms/transports) or issues credentials MUST call
        this before doing so. Channels that do not exist (e.g. the
        sentinel channel 0 used by /voice/ice-servers) are skipped here
        and handled by the caller's own channel lookup.

        Raises:
            PermissionDeniedError: If the user lacks voice.connect.
        """
        manager = self._voice_manager()
        if manager is None:
            return

        get_channel = getattr(manager, "_get_server_channel", None)
        require_permission = getattr(manager, "_require_permission", None)
        if get_channel is None or require_permission is None:
            return

        try:
            channel = get_channel(channel_id)
        except Exception as e:
            logger.warning(
                f"Could not resolve channel {channel_id} for connect check: {e}"
            )
            return

        if not channel:
            # Unknown/non-voice channel: defer to the caller's own
            # authorization path rather than failing open silently here.
            return

        server_id = channel.get("server_id")
        if server_id is None:
            return

        require_permission(user_id, server_id, "voice.connect", channel_id)

    def get_ice_servers(self, user_id: int) -> List[Any]:
        """
        Build the ICE server list for a user WITHOUT minting a session or
        media token.

        The legacy pattern routed ``GET /voice/ice-servers`` through
        ``get_voice_server_info(user_id, 0)``: the sentinel channel made
        ``get_voice_channel(0)`` return None, which raised NotConnectedError
        that the route's blanket ``except`` swallowed, so the endpoint ALWAYS
        fell back to public STUN defaults and configured TURN credentials
        were never delivered there. This method returns exactly what a
        pre-join ICE bootstrap needs: the configured STUN/TURN servers.
        """
        ice_builder: Any = self._ice_builder
        return ice_builder.build(user_id)

    def get_voice_server_info(self, user_id: int, channel_id: int) -> VoiceServerInfo:
        """
        Get voice server connection info including TURN credentials.

        Args:
            user_id: User ID
            channel_id: Voice channel ID

        Returns:
            VoiceServerInfo with connection details
        """
        # SECURITY (Agent 34 M6): issuing sessions/tokens/TURN credentials to
        # view-only members let anyone who could *see* a channel obtain
        # media-plane credentials. Require voice.connect for credential
        # issuance (unknown channels are skipped — see helper docstring).
        self._require_connect_permission(user_id, channel_id)

        ice_servers = self._ice_builder.build(user_id)

        session_id = self._generate_session_id()

        bitrate = 64000
        if self._voice:
            channel = self._voice.get_voice_channel(channel_id, user_id)
            if not channel:
                logger.warning(
                    f"Unauthorized access attempt to voice server info for channel {channel_id} by user {user_id}"
                )
                raise NotConnectedError(f"Access denied to channel {channel_id}")
            bitrate = channel.bitrate

        mediasoup_url = self._sfu_config.get("ws_url", "wss://localhost:4443")

        # SECURITY (Agent 34 C4): the media-plane endpoint previously embedded
        # only roomId/peerId with NO auth material. Mint a bearer token,
        # persist it for later verification, and attach it to the endpoint so
        # the media plane can authenticate the bearer.
        token = secrets.token_urlsafe(32)
        now = self._get_timestamp()
        self._media_tokens[token] = {
            "user_id": user_id,
            "channel_id": channel_id,
            "session_id": session_id,
            "issued_at": now,
            "expires_at": now + MEDIA_TOKEN_TTL_MS,
        }

        from urllib.parse import urlencode

        endpoint = f"{mediasoup_url}/?{urlencode({'roomId': f'voice_{channel_id}', 'peerId': f'user_{user_id}', 'token': token})}"

        return VoiceServerInfo(
            endpoint=endpoint,
            token=token,
            ice_servers=ice_servers,
            session_id=session_id,
            channel_id=channel_id,
            user_id=user_id,
            bitrate=bitrate,
        )

    def validate_media_token(
        self,
        token: Optional[str],
        user_id: Optional[int] = None,
        channel_id: Optional[int] = None,
    ) -> bool:
        """
        Verify a media-plane bearer token against the issuance registry.

        Args:
            token: The bearer token presented by the client
            user_id: Optional user the token must belong to
            channel_id: Optional channel the token must be bound to

        Returns:
            True only if the token exists, is unexpired, and matches any
            supplied user/channel constraints.
        """
        if not token:
            return False

        entry = self._media_tokens.get(token)
        if not entry:
            return False

        now = self._get_timestamp()
        if entry.get("expires_at", 0) < now:
            # Expired tokens are garbage collected on sight.
            self._media_tokens.pop(token, None)
            return False

        if user_id is not None and entry.get("user_id") != user_id:
            return False
        if channel_id is not None and entry.get("channel_id") != channel_id:
            return False

        return True

    def revoke_media_token(self, token: Optional[str]) -> bool:
        """Revoke a previously issued media-plane token."""
        if not token:
            return False
        return self._media_tokens.pop(token, None) is not None

    def create_voice_connection(self, user_id: int, channel_id: int) -> VoiceServerInfo:
        """
        Create a new voice connection for a user.

        Args:
            user_id: User ID
            channel_id: Voice channel ID

        Returns:
            VoiceServerInfo with connection details

        Raises:
            AlreadyConnectedError: If user already has a voice connection
        """
        if user_id in self._connections:
            existing = self._connections[user_id]
            logger.debug(
                f"User {user_id} already has voice connection (state: {existing.state})"
            )
            raise AlreadyConnectedError(
                f"User {user_id} already connected to voice",
                user_id=user_id,
                channel_id=channel_id,
            )

        info = self.get_voice_server_info(user_id, channel_id)

        now = self._get_timestamp()
        connection = VoiceConnection(
            user_id=user_id,
            channel_id=channel_id,
            session_id=info.session_id,
            state=SignalingState.CONNECTING,
            created_at=now,
            last_activity=now,
            media_token=info.token,
        )

        self._connections[user_id] = connection

        logger.debug(
            f"Created voice connection for user {user_id} in channel {channel_id}"
        )

        return info

    def disconnect_voice(self, user_id: int, channel_id: Optional[int] = None) -> bool:
        """
        Disconnect a user from voice (sync wrapper).

        Args:
            user_id: User ID
            channel_id: Optional channel ID to verify

        Returns:
            True if disconnected
        """
        # H1 (Agent 34): snapshot the CURRENT session id BEFORE scheduling
        # the async teardown. If the user rejoins before the task runs, the
        # task would otherwise read the NEW connection and tear it down
        # (including revoking its fresh media token), locking the user out.
        expected_session_id = None
        connection = self._connections.get(user_id)
        if connection:
            expected_session_id = connection.session_id

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            asyncio.create_task(
                self.disconnect_voice_async(
                    user_id, channel_id, expected_session_id=expected_session_id
                )
            )
            return True

        return asyncio.run(self.disconnect_voice_async(user_id, channel_id))

    async def disconnect_voice_async(
        self,
        user_id: int,
        channel_id: Optional[int] = None,
        *,
        expected_session_id: Optional[str] = None,
    ) -> bool:
        """
        Disconnect a user from voice (async version that cleans up SFU).

        Args:
            user_id: User ID
            channel_id: Optional channel ID to verify
            expected_session_id: Session id observed by the caller when the
                disconnect was requested. When supplied and the live
                connection has a DIFFERENT session (the user already
                rejoined), the cleanup is skipped entirely so the new
                session's state/token survive.

        Returns:
            True if disconnected
        """
        connection = self._connections.get(user_id)
        if not connection:
            return False

        if channel_id and connection.channel_id != channel_id:
            return False

        if (
            expected_session_id is not None
            and connection.session_id != expected_session_id
        ):
            # A rejoin raced this disconnect: the live connection belongs to
            # a NEWER session. Do NOT clean it up or revoke its token.
            logger.info(
                f"Skipping stale voice disconnect for user {user_id}: session "
                f"{expected_session_id} was replaced by {connection.session_id}"
            )
            return False

        # H1 (Agent 34): snapshot EVERYTHING needed for cleanup BEFORE any
        # await point. Previously the local mapping was deleted AFTER
        # ``await sfu.leave_room``, so a user who rejoined while the leave
        # was in-flight had their NEWER connection deleted by this stale
        # cleanup. Guard all post-await work by session id.
        session_id = connection.session_id
        sfu_room_id = connection.sfu_room_id
        sfu_peer_id = connection.sfu_peer_id
        media_token = connection.media_token

        connection.state = SignalingState.DISCONNECTING

        # Local state is cleaned synchronously BEFORE the await so a rejoin
        # racing this disconnect can create a fresh connection immediately.
        self._cleanup_local_connection(user_id, channel_id)

        # Revoke the media-plane token bound to this session (C4).
        if media_token:
            self.revoke_media_token(media_token)

        if sfu_room_id and sfu_peer_id:
            try:
                sfu = self._get_sfu()
                await sfu.leave_room(sfu_room_id, sfu_peer_id)
                logger.debug(
                    f"Left SFU room {sfu_room_id} for user {user_id} "
                    f"(session {session_id})"
                )
            except Exception as e:
                logger.warning(f"Failed to leave SFU room for user {user_id}: {e}")

        logger.debug(f"User {user_id} disconnected from voice (async)")
        return True

    def _cleanup_local_connection(
        self, user_id: int, channel_id: Optional[int] = None
    ) -> bool:
        """
        Clean up local connection state without SFU cleanup.

        Args:
            user_id: User ID
            channel_id: Optional channel ID to verify

        Returns:
            True if cleaned up
        """
        connection = self._connections.get(user_id)
        if not connection:
            return False

        if channel_id and connection.channel_id != channel_id:
            return False

        connection.state = SignalingState.DISCONNECTING

        self._ice_manager.clear_candidates(connection.session_id)

        del self._connections[user_id]

        logger.debug(f"User {user_id} local voice connection cleaned up")

        return True

    def record_activity(self, user_id: int) -> bool:
        """Refresh ``last_activity`` for a user's signaling connection.

        H2 follow-up: SFU stats/keepalive traffic (quality reports,
        speaking indicators) proves a call is still alive even when no
        SDP/ICE renegotiation happens. Without refreshing the idle clock
        here, quiet-but-active calls would be falsely swept by
        :meth:`sweep_stale_connections`.

        Returns True when an active connection was touched.
        """
        connection = self._connections.get(user_id)
        if not connection:
            return False
        connection.last_activity = self._get_timestamp()
        return True

    async def sweep_stale_connections(
        self, max_idle_ms: int = 30 * 60 * 1000
    ) -> List[int]:
        """Disconnect signaling connections idle beyond ``max_idle_ms``.

        H2 (Agent 34): nothing scanned ``last_activity``, so connections whose
        gateway socket died (or whose VOICE_DISCONNECT never reached the
        signaling layer) kept SFU rooms/peers alive indefinitely. This sweeper
        performs a FULL disconnect (SFU leave + local cleanup + token revoke)
        for every stale connection and returns the affected user ids.

        Callers (e.g. the gateway heartbeat or a periodic maintenance task)
        should invoke this periodically.
        """
        now = self._get_timestamp()
        stale_users = [
            user_id
            for user_id, conn in list(self._connections.items())
            if now - conn.last_activity >= max_idle_ms
        ]
        for user_id in stale_users:
            try:
                await self.disconnect_voice_async(user_id)
                logger.info(f"Swept stale voice connection for user {user_id}")
            except Exception as e:
                logger.warning(
                    f"Failed to sweep stale voice connection for user {user_id}: {e}"
                )
        return stale_users

    def get_active_connections(self, channel_id: int) -> List[Dict[str, Any]]:
        """
        Get all active connections in a channel.

        Args:
            channel_id: Voice channel ID

        Returns:
            List of connection info dictionaries
        """
        connections = []

        for user_id, conn in self._connections.items():
            if conn.channel_id == channel_id and conn.state == SignalingState.CONNECTED:
                connections.append(
                    {
                        "user_id": user_id,
                        "session_id": conn.session_id,
                        "state": conn.state.value,
                        "screen_share": conn.screen_share.to_dict()
                        if conn.screen_share
                        else None,
                        "quality": conn.quality.to_dict() if conn.quality else None,
                    }
                )

        return connections
