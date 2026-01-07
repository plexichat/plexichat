"""
Signaling manager - Core business logic for WebRTC signaling.

Handles voice connections, SDP exchange, ICE relay, and SFU integration.

Supports multiple SFU backends:
- mediasoup-ws: WebSocket-based adapter for mediasoup-demo server (recommended)
- mediasoup: REST API adapter for custom mediasoup servers
- janus: REST API adapter for Janus Gateway
"""

import asyncio
import time
import secrets
from typing import Dict, List, Optional, Any

import utils.logger as logger

from .models import (
    SDPType,
    SDPMessage,
    TURNCredentials,
    VoiceServerInfo,
    ConnectionQuality,
    QualityLevel,
    ScreenShareState,
    SignalingState,
    VoiceConnection,
    QUALITY_BITRATE_THRESHOLDS,
)
from .exceptions import (
    SDPError,
    NotConnectedError,
    ScreenShareError,
)
from .sdp import parse_sdp, validate_sdp, SDPManipulator
from .ice import ICECandidateManager, parse_ice_candidate
from .turn import ICEServerBuilder
from .sfu import create_adapter, SFUAdapter
from .sfu.base import TransportDirection


class SignalingManager:
    """Core signaling manager handling all WebRTC operations."""

    def __init__(
        self,
        voice_module=None,
        events_module=None,
        sfu_backend: str = "mediasoup-ws",
        mediasoup_url: str = "wss://localhost:4443",
        mediasoup_origin: str = "https://localhost",
        janus_url: str = "http://localhost:8088/janus",
        stun_urls: Optional[List[str]] = None,
        turn_urls: Optional[List[str]] = None,
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
            sfu_backend: SFU backend to use (mediasoup-ws, mediasoup, janus)
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
        self._voice = voice_module
        self._events = events_module
        self._sfu_backend = sfu_backend
        self._mediasoup_origin = mediasoup_origin

        # Build SFU config based on backend
        if sfu_backend == "mediasoup-ws":
            # Convert https:// to wss:// if needed
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

        self._sfu: Optional[SFUAdapter] = None

        # ICE server builder
        self._ice_builder = ICEServerBuilder(
            stun_urls=stun_urls,
            turn_urls=turn_urls,
            turn_secret=turn_secret,
            turn_ttl=turn_ttl,
            turn_username=turn_username,
            turn_credential=turn_credential,
        )

        # ICE candidate manager
        self._ice_manager = ICECandidateManager()

        # SDP manipulator
        self._sdp_manipulator = SDPManipulator()

        # Active connections: {user_id: VoiceConnection}
        self._connections: Dict[int, VoiceConnection] = {}

        # Room mappings: {channel_id: room_id}
        self._rooms: Dict[int, str] = {}

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

    def get_voice_server_info(self, user_id: int, channel_id: int) -> VoiceServerInfo:
        """
        Get voice server connection info including TURN credentials.

        Args:
            user_id: User ID
            channel_id: Voice channel ID

        Returns:
            VoiceServerInfo with connection details
        """
        # Get ICE servers with TURN credentials
        ice_servers = self._ice_builder.build(user_id)

        # Generate session ID
        session_id = self._generate_session_id()

        # Get channel bitrate from voice module
        bitrate = 64000
        if self._voice:
            channel = self._voice.get_voice_channel(channel_id, user_id)
            if not channel:
                logger.warning(
                    f"Unauthorized access attempt to voice server info for channel {channel_id} by user {user_id}"
                )
                from .exceptions import NotConnectedError

                raise NotConnectedError(f"Access denied to channel {channel_id}")
            bitrate = channel.bitrate

        # Build endpoint URL using the mediasoup server URL
        # The actual WebRTC connection uses ICE candidates from the SFU transport
        mediasoup_url = self._sfu_config.get("ws_url", "wss://localhost:4443")
        endpoint = f"{mediasoup_url}/?roomId=voice_{channel_id}&peerId=user_{user_id}"

        # Generate connection token
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
        """
        # Clean up any existing connection first (handles reconnects gracefully)
        if user_id in self._connections:
            existing = self._connections[user_id]
            logger.debug(
                f"Cleaning up existing voice connection for user {user_id} (state: {existing.state})"
            )
            # Clean up local state - SFU cleanup will happen async
            self._cleanup_local_connection(user_id)

        # Get server info
        info = self.get_voice_server_info(user_id, channel_id)

        # Create connection record
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

    async def handle_sdp_offer_async(
        self,
        user_id: int,
        channel_id: int,
        sdp: str,
        sdp_type: str = "offer",
    ) -> SDPMessage:
        """
        Handle an SDP offer from a client (async version that uses SFU).

        Args:
            user_id: User ID
            channel_id: Voice channel ID
            sdp: SDP string
            sdp_type: SDP type (offer/answer)

        Returns:
            SDPMessage with answer
        """
        # Get or create connection
        connection = self._connections.get(user_id)
        if not connection:
            # Auto-create connection
            self.create_voice_connection(user_id, channel_id)
            connection = self._connections[user_id]

        # Parse and validate SDP
        try:
            parsed_type = SDPType(sdp_type)
            validate_sdp(sdp, parsed_type)
        except Exception as e:
            raise SDPError(f"Invalid SDP: {e}")

        # Store remote SDP
        connection.remote_sdp = sdp
        connection.last_activity = self._get_timestamp()

        # Get channel bitrate
        bitrate = 64000
        if self._voice:
            channel = self._voice.get_voice_channel(channel_id, user_id)
            if not channel:
                logger.warning(
                    f"Unauthorized SDP offer for channel {channel_id} by user {user_id}"
                )
                from .exceptions import NotConnectedError

                raise NotConnectedError(f"Access denied to channel {channel_id}")
            bitrate = channel.bitrate

        # Modify SDP for bitrate
        modified_sdp = self._sdp_manipulator.set_bitrate(sdp, bitrate)

        # Try to get SDP answer from SFU
        try:
            answer_sdp = await self._get_sfu_answer(
                user_id, channel_id, modified_sdp, connection.session_id
            )
            logger.info(f"Got SDP answer from SFU for user {user_id}")
        except Exception as e:
            logger.warning(f"Failed to get SFU answer, using fallback: {e}")
            # Fallback to generated answer (won't work for actual media, but allows testing)
            answer_sdp = self._generate_answer_sdp(modified_sdp, connection.session_id)

        connection.local_sdp = answer_sdp

        # Update state
        connection.state = SignalingState.CONNECTING

        logger.debug(f"Processed SDP offer from user {user_id}")

        return SDPMessage(
            sdp_type=SDPType.ANSWER,
            sdp=answer_sdp,
            session_id=connection.session_id,
        )

    def handle_sdp_offer(
        self,
        user_id: int,
        channel_id: int,
        sdp: str,
        sdp_type: str = "offer",
    ) -> SDPMessage:
        """
        Handle an SDP offer from a client (sync wrapper).

        Args:
            user_id: User ID
            channel_id: Voice channel ID
            sdp: SDP string
            sdp_type: SDP type (offer/answer)

        Returns:
            SDPMessage with answer
        """
        # Try to run async version
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're in an async context, create a task
                asyncio.ensure_future(
                    self.handle_sdp_offer_async(user_id, channel_id, sdp, sdp_type)
                )
                # Can't await here in sync context, so use fallback
                # The WebSocket handler should call handle_sdp_offer_async directly
                raise RuntimeError("Use handle_sdp_offer_async in async context")
            else:
                return loop.run_until_complete(
                    self.handle_sdp_offer_async(user_id, channel_id, sdp, sdp_type)
                )
        except RuntimeError as e:
            # No event loop or already running - use sync fallback
            logger.debug(f"SDP offer async handle failed, using sync fallback: {e}")

        # Sync fallback (generates fake SDP - won't work for real media)
        connection = self._connections.get(user_id)
        if not connection:
            self.create_voice_connection(user_id, channel_id)
            connection = self._connections[user_id]

        try:
            parsed_type = SDPType(sdp_type)
            validate_sdp(sdp, parsed_type)
        except Exception as e:
            raise SDPError(f"Invalid SDP: {e}")

        connection.remote_sdp = sdp
        connection.last_activity = self._get_timestamp()

        bitrate = 64000
        if self._voice:
            channel = self._voice.get_voice_channel(channel_id, user_id)
            if channel:
                bitrate = channel.bitrate

        modified_sdp = self._sdp_manipulator.set_bitrate(sdp, bitrate)
        answer_sdp = self._generate_answer_sdp(modified_sdp, connection.session_id)
        connection.local_sdp = answer_sdp
        connection.state = SignalingState.CONNECTING

        logger.debug(f"Processed SDP offer from user {user_id} (sync fallback)")

        return SDPMessage(
            sdp_type=SDPType.ANSWER,
            sdp=answer_sdp,
            session_id=connection.session_id,
        )

    async def _get_sfu_answer(
        self,
        user_id: int,
        channel_id: int,
        offer_sdp: str,
        session_id: str,
    ) -> str:
        """
        Get SDP answer from the SFU by creating a transport.

        Args:
            user_id: User ID
            channel_id: Voice channel ID
            offer_sdp: Client's SDP offer
            session_id: Session ID

        Returns:
            SDP answer string
        """
        sfu = self._get_sfu()
        room_id = self._get_room_id(channel_id)
        peer_id = f"user_{user_id}"

        # Join the room and get router capabilities
        logger.debug(f"Joining SFU room {room_id} as peer {peer_id}")
        room_info = await sfu.join_room(room_id, peer_id)
        router_caps = room_info.get("routerRtpCapabilities", {})

        # Create a send transport for this peer (to send audio to SFU)
        # mediasoup-demo only accepts "send" or "recv", not "sendrecv"
        logger.debug(f"Creating send transport for peer {peer_id}")
        transport = await sfu.create_transport(
            room_id, peer_id, TransportDirection.SEND
        )

        # Store transport info in connection
        connection = self._connections.get(user_id)
        if connection:
            connection.transport_id = transport.id
            connection.sfu_room_id = room_id
            connection.sfu_peer_id = peer_id

        # Complete the join process with RTP capabilities
        # This is required by mediasoup-demo or it will close the peer after 10 seconds
        logger.debug(f"Completing join for peer {peer_id}")
        await sfu.complete_join(
            room_id,
            peer_id,
            rtp_capabilities=router_caps,  # Use router caps as client caps for now
            display_name=f"User {user_id}",
        )

        # Build SDP answer from transport parameters
        answer_sdp = self._build_sdp_from_transport(offer_sdp, transport, session_id)

        return answer_sdp

    def _build_sdp_from_transport(
        self, offer_sdp: str, transport, session_id: str
    ) -> str:
        """
        Build an SDP answer from mediasoup transport parameters.

        Args:
            offer_sdp: Client's SDP offer
            transport: SFUTransport with ICE/DTLS parameters
            session_id: Session ID

        Returns:
            SDP answer string
        """
        parsed = parse_sdp(offer_sdp)

        # Convert hex session_id to numeric for SDP
        numeric_session_id = str(int(session_id[:16], 16) % (10**18))

        # Extract ICE parameters from transport
        ice_params = transport.ice_parameters
        ice_ufrag = ice_params.get("usernameFragment", secrets.token_hex(4))
        ice_pwd = ice_params.get("password", secrets.token_hex(12))

        # Extract DTLS parameters
        dtls_params = transport.dtls_parameters
        fingerprints = dtls_params.get("fingerprints", [])
        fingerprint = (
            fingerprints[0]
            if fingerprints
            else {
                "algorithm": "sha-256",
                "value": ":".join([secrets.token_hex(1).upper() for _ in range(32)]),
            }
        )
        dtls_role = dtls_params.get("role", "auto")

        # Map DTLS role to SDP setup attribute
        # For an SDP answer, we should use "active" (we initiate DTLS) or "passive"
        # "actpass" is only valid in offers, not answers
        setup_map = {"auto": "active", "server": "passive", "client": "active"}
        setup = setup_map.get(dtls_role, "active")

        # Build answer
        lines = [
            "v=0",
            f"o=- {numeric_session_id} 2 IN IP4 127.0.0.1",
            "s=-",
            "t=0 0",
            "a=group:BUNDLE 0",
            "a=msid-semantic: WMS",
        ]

        # Process media sections
        for idx, media in enumerate(parsed.get("media", [])):
            media_type = media.get("type", "audio")
            port = media.get("port", 9)
            protocol = media.get("protocol", "UDP/TLS/RTP/SAVPF")
            formats = media.get("formats", ["111"])

            lines.append(f"m={media_type} {port} {protocol} {' '.join(formats)}")
            lines.append("c=IN IP4 0.0.0.0")
            lines.append("a=rtcp:9 IN IP4 0.0.0.0")

            # ICE attributes from transport
            lines.append(f"a=ice-ufrag:{ice_ufrag}")
            lines.append(f"a=ice-pwd:{ice_pwd}")
            lines.append("a=ice-options:trickle")

            # Add ICE candidates from transport
            for candidate in transport.ice_candidates:
                cand_str = self._format_ice_candidate(candidate)
                if cand_str:
                    lines.append(f"a={cand_str}")

            # DTLS fingerprint and setup
            lines.append(
                f"a=fingerprint:{fingerprint.get('algorithm', 'sha-256')} {fingerprint.get('value', '')}"
            )
            lines.append(f"a=setup:{setup}")
            lines.append(f"a=mid:{idx}")

            # Direction and rtcp-mux
            lines.append("a=sendrecv")
            lines.append("a=rtcp-mux")
            lines.append("a=rtcp-rsize")

            # Copy codec info from offer
            media_attrs = media.get("attributes", {})
            for fmt in formats:
                rtpmap = media_attrs.get(f"rtpmap:{fmt}") or media_attrs.get("rtpmap")
                if rtpmap:
                    if isinstance(rtpmap, list):
                        for r in rtpmap:
                            if r.startswith(fmt):
                                lines.append(f"a=rtpmap:{r}")
                    else:
                        lines.append(f"a=rtpmap:{fmt} {rtpmap}")
                elif fmt == "111" and media_type == "audio":
                    lines.append("a=rtpmap:111 opus/48000/2")
                    lines.append("a=fmtp:111 minptime=10;useinbandfec=1")

        return "\r\n".join(lines) + "\r\n"

    def _format_ice_candidate(self, candidate: dict) -> str:
        """Format an ICE candidate dict as an SDP candidate line."""
        try:
            foundation = candidate.get("foundation", "1")
            component = candidate.get("component", 1)
            protocol = candidate.get("protocol", "udp").lower()
            priority = candidate.get("priority", 2130706431)
            ip = candidate.get("ip", candidate.get("address", ""))
            port = candidate.get("port", 0)
            typ = candidate.get("type", "host")

            if not ip or not port:
                return ""

            cand = f"candidate:{foundation} {component} {protocol} {priority} {ip} {port} typ {typ}"

            # Add related address/port for srflx/relay candidates
            if typ in ("srflx", "relay", "prflx"):
                raddr = candidate.get("relatedAddress", candidate.get("raddr", ""))
                rport = candidate.get("relatedPort", candidate.get("rport", 0))
                if raddr and rport:
                    cand += f" raddr {raddr} rport {rport}"

            # Add tcptype for TCP candidates
            if protocol == "tcp":
                tcptype = candidate.get("tcpType", "passive")
                cand += f" tcptype {tcptype}"

            return cand
        except Exception as e:
            logger.warning(f"Failed to format ICE candidate: {e}")
            return ""

    def _generate_answer_sdp(self, offer_sdp: str, session_id: str) -> str:
        """
        Generate an SDP answer from an offer.

        In production, this would be generated by the SFU.
        This is a simplified implementation for signaling flow.
        """
        # Parse offer to extract key parameters
        parsed = parse_sdp(offer_sdp)

        # Convert hex session_id to numeric for SDP (SDP requires numeric session ID)
        # Use first 16 chars of hex and convert to int
        numeric_session_id = str(int(session_id[:16], 16) % (10**18))

        # Generate ICE credentials (always required)
        ice_ufrag = secrets.token_hex(4)
        ice_pwd = secrets.token_hex(12)
        # Generate DTLS fingerprint
        fingerprint = ":".join([secrets.token_hex(1).upper() for _ in range(32)])

        # Build answer (simplified)
        lines = [
            "v=0",
            f"o=- {numeric_session_id} 2 IN IP4 127.0.0.1",
            "s=-",
            "t=0 0",
            "a=group:BUNDLE 0",
            "a=msid-semantic: WMS",
        ]

        # Process media sections
        for idx, media in enumerate(parsed.get("media", [])):
            media_type = media.get("type", "audio")
            port = media.get("port", 9)
            protocol = media.get("protocol", "UDP/TLS/RTP/SAVPF")
            formats = media.get("formats", ["111"])

            lines.append(f"m={media_type} {port} {protocol} {' '.join(formats)}")
            lines.append("c=IN IP4 0.0.0.0")
            lines.append("a=rtcp:9 IN IP4 0.0.0.0")

            # ICE attributes (required at media level)
            lines.append(f"a=ice-ufrag:{ice_ufrag}")
            lines.append(f"a=ice-pwd:{ice_pwd}")
            lines.append("a=ice-options:trickle")
            lines.append(f"a=fingerprint:sha-256 {fingerprint}")
            lines.append("a=setup:active")
            lines.append(f"a=mid:{idx}")

            # Add direction
            lines.append("a=sendrecv")

            # Add rtcp-mux
            lines.append("a=rtcp-mux")

            # Copy codec info from offer
            media_attrs = media.get("attributes", {})
            for fmt in formats:
                rtpmap = media_attrs.get(f"rtpmap:{fmt}") or media_attrs.get("rtpmap")
                if rtpmap:
                    if isinstance(rtpmap, list):
                        for r in rtpmap:
                            if r.startswith(fmt):
                                lines.append(f"a=rtpmap:{r}")
                    else:
                        lines.append(f"a=rtpmap:{fmt} {rtpmap}")
                # Add default opus codec if no rtpmap found
                elif fmt == "111" and media_type == "audio":
                    lines.append("a=rtpmap:111 opus/48000/2")
                    lines.append("a=fmtp:111 minptime=10;useinbandfec=1")

        return "\r\n".join(lines) + "\r\n"

    def handle_ice_candidate(
        self,
        user_id: int,
        channel_id: int,
        candidate: str,
        sdp_mid: Optional[str] = None,
        sdp_mline_index: Optional[int] = None,
    ) -> bool:
        """
        Handle an ICE candidate from a client.

        Args:
            user_id: User ID
            channel_id: Voice channel ID
            candidate: ICE candidate string
            sdp_mid: Media stream ID
            sdp_mline_index: Media line index

        Returns:
            True if processed successfully
        """
        connection = self._connections.get(user_id)
        if not connection:
            raise NotConnectedError(
                "User not connected to voice", user_id=user_id, channel_id=channel_id
            )

        # Parse and validate candidate
        ice_candidate = parse_ice_candidate(candidate, sdp_mid, sdp_mline_index)

        # Store candidate
        self._ice_manager.add_candidate(
            connection.session_id, candidate, sdp_mid, sdp_mline_index
        )
        connection.ice_candidates.append(ice_candidate)
        connection.last_activity = self._get_timestamp()

        # If we have enough candidates, mark as connected
        if (
            len(connection.ice_candidates) >= 1
            and connection.state == SignalingState.CONNECTING
        ):
            connection.state = SignalingState.CONNECTED
            logger.debug(f"User {user_id} voice connection established")

        return True

    def disconnect_voice(self, user_id: int, channel_id: Optional[int] = None) -> bool:
        """
        Disconnect a user from voice (sync wrapper).

        Args:
            user_id: User ID
            channel_id: Optional channel ID to verify

        Returns:
            True if disconnected
        """
        # Try to run async version
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Schedule the async cleanup but don't wait
                asyncio.create_task(self.disconnect_voice_async(user_id, channel_id))
                return True
            else:
                return loop.run_until_complete(
                    self.disconnect_voice_async(user_id, channel_id)
                )
        except RuntimeError as e:
            # No event loop - do sync cleanup only
            logger.debug(f"Disconnect voice async failed, using sync cleanup: {e}")

        # Sync fallback - just clean up local state
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

        # Update state
        connection.state = SignalingState.DISCONNECTING

        # Clean up SFU connection if we have one
        if connection.sfu_room_id and connection.sfu_peer_id:
            try:
                sfu = self._get_sfu()
                await sfu.leave_room(connection.sfu_room_id, connection.sfu_peer_id)
                logger.debug(
                    f"Left SFU room {connection.sfu_room_id} for user {user_id}"
                )
            except Exception as e:
                logger.warning(f"Failed to leave SFU room for user {user_id}: {e}")

        # Clean up local state
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

        # Update state
        connection.state = SignalingState.DISCONNECTING

        # Clear ICE candidates
        self._ice_manager.clear_candidates(connection.session_id)

        # Remove connection
        del self._connections[user_id]

        logger.debug(f"User {user_id} local voice connection cleaned up")

        return True

    def get_turn_credentials(self, user_id: int) -> TURNCredentials:
        """
        Get TURN server credentials for a user.

        Args:
            user_id: User ID

        Returns:
            TURNCredentials
        """
        creds = self._ice_builder.get_turn_credentials(user_id)
        if not creds:
            # Return empty credentials if TURN not configured
            return TURNCredentials(
                username="",
                credential="",
                urls=[],
                ttl=0,
                expires_at=0,
            )
        return creds

    def start_screen_share(self, user_id: int, channel_id: int) -> ScreenShareState:
        """
        Start screen sharing for a user.

        Args:
            user_id: User ID
            channel_id: Voice channel ID

        Returns:
            ScreenShareState
        """
        connection = self._connections.get(user_id)
        if not connection:
            raise NotConnectedError(
                "User not connected to voice", user_id=user_id, channel_id=channel_id
            )

        if connection.channel_id != channel_id:
            raise ScreenShareError(
                "User not in specified channel",
                user_id=user_id,
                reason="channel_mismatch",
            )

        if connection.screen_share and connection.screen_share.active:
            raise ScreenShareError(
                "Screen share already active", user_id=user_id, reason="already_sharing"
            )

        now = self._get_timestamp()
        stream_id = f"screen_{user_id}_{now}"

        screen_share = ScreenShareState(
            user_id=user_id,
            channel_id=channel_id,
            active=True,
            stream_id=stream_id,
            started_at=now,
        )

        connection.screen_share = screen_share
        connection.last_activity = now

        # Update voice state if voice module available
        if self._voice:
            try:
                self._voice.set_streaming(user_id, True)
            except Exception as e:
                logger.warning(
                    f"Failed to update voice streaming state for user {user_id}: {e}"
                )

        logger.debug(f"User {user_id} started screen share")

        return screen_share

    def stop_screen_share(self, user_id: int, channel_id: int) -> bool:
        """
        Stop screen sharing for a user.

        Args:
            user_id: User ID
            channel_id: Voice channel ID

        Returns:
            True if stopped
        """
        connection = self._connections.get(user_id)
        if not connection:
            return False

        if not connection.screen_share or not connection.screen_share.active:
            return False

        connection.screen_share.active = False
        connection.last_activity = self._get_timestamp()

        # Update voice state if voice module available
        if self._voice:
            try:
                self._voice.set_streaming(user_id, False)
            except Exception as e:
                logger.warning(
                    f"Failed to stop voice streaming state for user {user_id}: {e}"
                )

        logger.debug(f"User {user_id} stopped screen share")

        return True

    def get_connection_quality(
        self, user_id: int, channel_id: int
    ) -> ConnectionQuality:
        """
        Get connection quality metrics for a user.

        Args:
            user_id: User ID
            channel_id: Voice channel ID

        Returns:
            ConnectionQuality
        """
        connection = self._connections.get(user_id)
        if not connection:
            raise NotConnectedError(
                "User not connected to voice", user_id=user_id, channel_id=channel_id
            )

        # Return cached quality or default
        if connection.quality:
            return connection.quality

        # Return default quality metrics
        return ConnectionQuality(
            user_id=user_id,
            channel_id=channel_id,
            quality_level=QualityLevel.GOOD,
            bitrate=64000,
            packet_loss=0.0,
            jitter=0.0,
            round_trip_time=50,
            timestamp=self._get_timestamp(),
        )

    def update_quality_hint(
        self,
        user_id: int,
        channel_id: int,
        target_bitrate: Optional[int] = None,
        quality_level: Optional[str] = None,
    ) -> bool:
        """
        Update quality hints for a connection.

        Args:
            user_id: User ID
            channel_id: Voice channel ID
            target_bitrate: Target bitrate in bps
            quality_level: Quality level name

        Returns:
            True if updated
        """
        connection = self._connections.get(user_id)
        if not connection:
            return False

        now = self._get_timestamp()

        # Determine quality level
        level = QualityLevel.GOOD
        if quality_level:
            try:
                level = QualityLevel(quality_level)
            except ValueError:
                logger.warning(
                    f"Invalid quality level: {quality_level}, defaulting to GOOD"
                )
                level = QualityLevel.GOOD

        # Determine bitrate
        bitrate = target_bitrate or 64000
        if not target_bitrate and quality_level:
            thresholds = QUALITY_BITRATE_THRESHOLDS.get(level, {})
            bitrate = thresholds.get("max", 64000)

        # Update quality
        connection.quality = ConnectionQuality(
            user_id=user_id,
            channel_id=channel_id,
            quality_level=level,
            bitrate=bitrate,
            packet_loss=0.0,
            jitter=0.0,
            round_trip_time=50,
            timestamp=now,
        )
        connection.last_activity = now

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
