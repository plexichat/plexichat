"""
Signaling manager - Core business logic for WebRTC signaling.

Handles voice connections, SDP exchange, ICE relay, and SFU integration.
"""

import time
import secrets
from typing import Dict, List, Optional, Any

import utils.logger as logger

from .models import (
    SDPType,
    SDPMessage,
    ICECandidate,
    TURNCredentials,
    VoiceServerInfo,
    ConnectionQuality,
    QualityLevel,
    ScreenShareState,
    SignalingState,
    VoiceConnection,
    ICEServer,
    QUALITY_BITRATE_THRESHOLDS,
)
from .exceptions import (
    SignalingError,
    SDPError,
    NotConnectedError,
    AlreadyConnectedError,
    ScreenShareError,
)
from .sdp import parse_sdp, validate_sdp, SDPManipulator
from .ice import ICECandidateManager, parse_ice_candidate
from .turn import ICEServerBuilder
from .sfu import create_adapter, SFUAdapter


class SignalingManager:
    """Core signaling manager handling all WebRTC operations."""
    
    def __init__(
        self,
        voice_module=None,
        events_module=None,
        sfu_backend: str = "mediasoup",
        mediasoup_url: str = "http://localhost:3000",
        janus_url: str = "http://localhost:8088/janus",
        stun_urls: Optional[List[str]] = None,
        turn_urls: Optional[List[str]] = None,
        turn_secret: str = "",
        turn_ttl: int = 86400,
    ):
        """
        Initialize the signaling manager.
        
        Args:
            voice_module: Voice module for state management
            events_module: Events module for dispatching events
            sfu_backend: SFU backend to use
            mediasoup_url: Mediasoup API URL
            janus_url: Janus API URL
            stun_urls: List of STUN server URLs
            turn_urls: List of TURN server URLs
            turn_secret: Shared secret for TURN credentials
            turn_ttl: TURN credential TTL in seconds
        """
        self._voice = voice_module
        self._events = events_module
        self._sfu_backend = sfu_backend
        
        # Create SFU adapter
        sfu_url = mediasoup_url if sfu_backend == "mediasoup" else janus_url
        self._sfu: Optional[SFUAdapter] = None
        self._sfu_config = {
            "backend": sfu_backend,
            "api_url": sfu_url,
        }
        
        # ICE server builder
        self._ice_builder = ICEServerBuilder(
            stun_urls=stun_urls,
            turn_urls=turn_urls,
            turn_secret=turn_secret,
            turn_ttl=turn_ttl,
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
            if channel:
                bitrate = channel.bitrate
        
        # Build endpoint URL
        endpoint = f"wss://voice.plexichat.com/ws/{channel_id}"
        
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
        # Check if already connected
        if user_id in self._connections:
            existing = self._connections[user_id]
            if existing.state in (SignalingState.CONNECTING, SignalingState.CONNECTED):
                raise AlreadyConnectedError(
                    "User already has an active voice connection",
                    user_id=user_id,
                    channel_id=existing.channel_id
                )
        
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
        
        logger.debug(f"Created voice connection for user {user_id} in channel {channel_id}")
        
        return info
    
    def handle_sdp_offer(
        self,
        user_id: int,
        channel_id: int,
        sdp: str,
        sdp_type: str = "offer",
    ) -> SDPMessage:
        """
        Handle an SDP offer from a client.
        
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
            if channel:
                bitrate = channel.bitrate
        
        # Modify SDP for bitrate
        modified_sdp = self._sdp_manipulator.set_bitrate(sdp, bitrate)
        
        # Generate answer SDP (simplified - in production this would come from SFU)
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
    
    def _generate_answer_sdp(self, offer_sdp: str, session_id: str) -> str:
        """
        Generate an SDP answer from an offer.
        
        In production, this would be generated by the SFU.
        This is a simplified implementation for signaling flow.
        """
        # Parse offer to extract key parameters
        parsed = parse_sdp(offer_sdp)
        
        # Build answer (simplified)
        lines = [
            "v=0",
            f"o=- {session_id} 2 IN IP4 127.0.0.1",
            "s=-",
            "t=0 0",
        ]
        
        # Copy session-level attributes
        attrs = parsed.get("attributes", {})
        if "ice-ufrag" in attrs:
            lines.append(f"a=ice-ufrag:{secrets.token_hex(4)}")
        if "ice-pwd" in attrs:
            lines.append(f"a=ice-pwd:{secrets.token_hex(12)}")
        if "fingerprint" in attrs:
            # Generate placeholder fingerprint
            fp = ":".join([secrets.token_hex(1).upper() for _ in range(32)])
            lines.append(f"a=fingerprint:sha-256 {fp}")
        
        lines.append("a=setup:active")
        
        # Process media sections
        for media in parsed.get("media", []):
            media_type = media.get("type", "audio")
            port = media.get("port", 9)
            protocol = media.get("protocol", "UDP/TLS/RTP/SAVPF")
            formats = media.get("formats", ["111"])
            
            lines.append(f"m={media_type} {port} {protocol} {' '.join(formats)}")
            lines.append("c=IN IP4 0.0.0.0")
            
            # Add direction
            lines.append("a=sendrecv")
            
            # Add rtcp-mux
            lines.append("a=rtcp-mux")
            
            # Copy codec info
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
                "User not connected to voice",
                user_id=user_id,
                channel_id=channel_id
            )
        
        # Parse and validate candidate
        ice_candidate = parse_ice_candidate(candidate, sdp_mid, sdp_mline_index)
        
        # Store candidate
        self._ice_manager.add_candidate(
            connection.session_id,
            candidate,
            sdp_mid,
            sdp_mline_index
        )
        connection.ice_candidates.append(ice_candidate)
        connection.last_activity = self._get_timestamp()
        
        # If we have enough candidates, mark as connected
        if len(connection.ice_candidates) >= 1 and connection.state == SignalingState.CONNECTING:
            connection.state = SignalingState.CONNECTED
            logger.debug(f"User {user_id} voice connection established")
        
        return True
    
    def disconnect_voice(self, user_id: int, channel_id: Optional[int] = None) -> bool:
        """
        Disconnect a user from voice.
        
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
        
        # Clear ICE candidates
        self._ice_manager.clear_candidates(connection.session_id)
        
        # Remove connection
        del self._connections[user_id]
        
        logger.debug(f"User {user_id} disconnected from voice")
        
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
                "User not connected to voice",
                user_id=user_id,
                channel_id=channel_id
            )
        
        if connection.channel_id != channel_id:
            raise ScreenShareError(
                "User not in specified channel",
                user_id=user_id,
                reason="channel_mismatch"
            )
        
        if connection.screen_share and connection.screen_share.active:
            raise ScreenShareError(
                "Screen share already active",
                user_id=user_id,
                reason="already_sharing"
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
            except Exception:
                pass
        
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
            except Exception:
                pass
        
        logger.debug(f"User {user_id} stopped screen share")
        
        return True
    
    def get_connection_quality(self, user_id: int, channel_id: int) -> ConnectionQuality:
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
                "User not connected to voice",
                user_id=user_id,
                channel_id=channel_id
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
                pass
        
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
                connections.append({
                    "user_id": user_id,
                    "session_id": conn.session_id,
                    "state": conn.state.value,
                    "screen_share": conn.screen_share.to_dict() if conn.screen_share else None,
                    "quality": conn.quality.to_dict() if conn.quality else None,
                })
        
        return connections
