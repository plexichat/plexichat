"""Base class for SignalingManager with core initialization and attributes."""

import secrets
import time
from typing import Any, Dict, Optional

import utils.logger as logger

from ..models import VoiceConnection
from ..sfu import create_adapter, SFUAdapter


class SignalingManagerBase:
    """Base class containing core initialization and shared state."""

    _voice: Optional[Any]
    _events: Optional[Any]
    _sfu_backend: str
    _mediasoup_origin: str
    _sfu_config: Dict[str, Any]
    _sfu: Optional[SFUAdapter]
    _ice_builder: Any
    _ice_manager: Any
    _sdp_manipulator: Any
    _connections: Dict[int, VoiceConnection]
    _rooms: Dict[int, str]

    def __init__(
        self,
        voice_module: Optional[Any] = None,
        events_module: Optional[Any] = None,
        sfu_backend: str = "aiortc",
        mediasoup_url: str = "wss://localhost:4443",
        mediasoup_origin: str = "https://localhost",
        janus_url: str = "http://localhost:8088/janus",
        stun_urls: Optional[list[str]] = None,
        turn_urls: Optional[list[str]] = None,
        turn_secret: str = "",
        turn_ttl: int = 86400,
        turn_username: str = "",
        turn_credential: str = "",
    ):
        """
        Initialize the signaling manager.

        Args:
            voice_module: Voice module for state management
            events_module: Events module for dispatching events
            sfu_backend: SFU backend to use (aiortc, mediasoup-ws, mediasoup, janus)
            mediasoup_url: Mediasoup server URL (WebSocket or REST)
            mediasoup_origin: Origin header for mediasoup-ws CORS
            janus_url: Janus API URL
            stun_urls: List of STUN server URLs
            turn_urls: List of TURN server URLs
            turn_secret: Shared secret for time-limited TURN credentials (coturn)
            turn_ttl: TURN credential TTL in seconds
            turn_username: Static TURN username (for services like metered.ca)
            turn_credential: Static TURN credential/password
        """
        from ..ice import ICECandidateManager
        from ..sdp import SDPManipulator
        from ..turn import ICEServerBuilder

        self._voice = voice_module
        self._events = events_module
        self._sfu_backend = sfu_backend
        self._mediasoup_origin = mediasoup_origin

        if sfu_backend == "aiortc":
            ice_servers = []
            if stun_urls:
                ice_servers.extend([{"urls": url} for url in stun_urls])
            if turn_urls:
                if turn_username and turn_credential:
                    ice_servers.extend(
                        [
                            {
                                "urls": url,
                                "username": turn_username,
                                "credential": turn_credential,
                            }
                            for url in turn_urls
                        ]
                    )
                elif turn_secret:
                    ice_servers.extend([{"urls": url} for url in turn_urls])

            self._sfu_config = {
                "backend": "aiortc",
                "ice_servers": ice_servers,
            }
        elif sfu_backend == "mediasoup-ws":
            ws_url = mediasoup_url
            if ws_url.startswith("https://"):
                ws_url = "wss://" + ws_url[8:]
            elif ws_url.startswith("http://"):
                ws_url = "ws://" + ws_url[7:]

            self._sfu_config = {
                "backend": "mediasoup-ws",
                "ws_url": ws_url,
                "origin": mediasoup_origin,
            }
        elif sfu_backend == "mediasoup":
            self._sfu_config = {
                "backend": "mediasoup",
                "api_url": mediasoup_url,
            }
        else:
            self._sfu_config = {
                "backend": sfu_backend,
                "api_url": janus_url,
            }

        self._sfu = None
        self._ice_builder = ICEServerBuilder(
            stun_urls=stun_urls,
            turn_urls=turn_urls,
            turn_secret=turn_secret,
            turn_ttl=turn_ttl,
            turn_username=turn_username,
            turn_credential=turn_credential,
        )
        self._ice_manager = ICECandidateManager()
        self._sdp_manipulator = SDPManipulator()
        self._connections = {}
        self._rooms = {}

        logger.info(f"Signaling manager initialized with {sfu_backend} backend")

    def _get_timestamp(self) -> int:
        """Get current timestamp in milliseconds."""
        return int(time.time() * 1000)

    def _generate_session_id(self) -> str:
        """Generate a unique session ID."""
        return secrets.token_hex(16)

    def _get_room_id(self, channel_id: int) -> str:
        """Get or create room ID for a channel."""
        if channel_id not in self._rooms:
            self._rooms[channel_id] = f"voice_{channel_id}"
        return self._rooms[channel_id]

    def _get_sfu(self) -> SFUAdapter:
        """Get or create SFU adapter."""
        if self._sfu is None:
            self._sfu = create_adapter(**self._sfu_config)
        return self._sfu
