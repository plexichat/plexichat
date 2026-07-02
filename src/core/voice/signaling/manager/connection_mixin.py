"""Connection management mixin for SignalingManager."""

import asyncio
from typing import List, Dict, Any, Optional

import utils.logger as logger

from ..exceptions import AlreadyConnectedError, NotConnectedError
from ..models import SignalingState, VoiceServerInfo, VoiceConnection


class ConnectionManagementMixin:
    """Mixin handling connection lifecycle methods."""

    _voice: Optional[Any]
    _connections: Dict[int, VoiceConnection]
    _ice_manager: Any
    _sfu_config: Dict[str, Any]
    _ice_builder: Any
    _get_sfu: Any

    def _get_timestamp(self) -> int: ...

    def _generate_session_id(self) -> str: ...

    def get_voice_server_info(self, user_id: int, channel_id: int) -> VoiceServerInfo:
        """
        Get voice server connection info including TURN credentials.

        Args:
            user_id: User ID
            channel_id: Voice channel ID

        Returns:
            VoiceServerInfo with connection details
        """
        from ..turn import ICEServerBuilder

        ice_builder: ICEServerBuilder = self._ice_builder
        ice_servers = ice_builder.build(user_id)

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

        import secrets

        mediasoup_url = self._sfu_config.get("ws_url", "wss://localhost:4443")
        endpoint = f"{mediasoup_url}/?roomId=voice_{channel_id}&peerId=user_{user_id}"

        token = secrets.token_urlsafe(32)

        return VoiceServerInfo(
            endpoint=endpoint,
            token=token,
            ice_servers=ice_servers,
            session_id=session_id,
            channel_id=channel_id,
            user_id=user_id,
            bitrate=bitrate,
        )

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
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(self.disconnect_voice_async(user_id, channel_id))
                return True
            else:
                return loop.run_until_complete(
                    self.disconnect_voice_async(user_id, channel_id)
                )
        except RuntimeError as e:
            logger.debug(f"Disconnect voice async failed, using sync cleanup: {e}")

        return self._cleanup_local_connection(user_id, channel_id)

    async def disconnect_voice_async(
        self, user_id: int, channel_id: Optional[int] = None
    ) -> bool:
        """
        Disconnect a user from voice (async version that cleans up SFU).

        Args:
            user_id: User ID
            channel_id: Optional channel ID to verify

        Returns:
            True if disconnected
        """
        connection = self._connections.get(user_id)
        if not connection:
            return False

        if channel_id and connection.channel_id != channel_id:
            return False

        connection.state = SignalingState.DISCONNECTING

        if connection.sfu_room_id and connection.sfu_peer_id:
            try:
                sfu = self._get_sfu()
                await sfu.leave_room(connection.sfu_room_id, connection.sfu_peer_id)
                logger.debug(
                    f"Left SFU room {connection.sfu_room_id} for user {user_id}"
                )
            except Exception as e:
                logger.warning(f"Failed to leave SFU room for user {user_id}: {e}")

        self._cleanup_local_connection(user_id, channel_id)

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
