"""
Voice signaling module - WebRTC signaling for voice/video connections.

Provides SDP exchange, ICE candidate relay, TURN credentials, and SFU integration.

Usage:
    from src.core.voice import signaling

    # Get voice server info for connecting
    info = signaling.get_voice_server_info(user_id, channel_id)

    # Handle SDP offer from client
    answer = signaling.handle_sdp_offer(user_id, channel_id, sdp_offer)

    # Relay ICE candidate
    signaling.handle_ice_candidate(user_id, channel_id, candidate)
"""

from typing import Optional, List, Dict, Any

from .models import (
    SDPType,
    SDPMessage,
    ICECandidate,
    ICEServer,
    TURNCredentials,
    VoiceServerInfo,
    ConnectionQuality,
    QualityLevel,
    ScreenShareState,
    SignalingState,
)
from .exceptions import (
    SignalingError,
    SDPError,
    SDPParseError,
    SDPValidationError,
    ICEError,
    ICECandidateError,
    TURNError,
    TURNCredentialError,
    SFUError,
    SFUConnectionError,
    SFUTimeoutError,
    ConnectionError,
    NotConnectedError,
    AlreadyConnectedError,
    ScreenShareError,
)

__all__ = [
    # Models
    "SDPType",
    "SDPMessage",
    "ICECandidate",
    "TURNCredentials",
    "VoiceServerInfo",
    "ConnectionQuality",
    "QualityLevel",
    "ScreenShareState",
    "SignalingState",
    # Exceptions
    "SignalingError",
    "SDPError",
    "SDPParseError",
    "SDPValidationError",
    "ICEError",
    "ICECandidateError",
    "TURNError",
    "TURNCredentialError",
    "SFUError",
    "SFUConnectionError",
    "SFUTimeoutError",
    "ConnectionError",
    "NotConnectedError",
    "AlreadyConnectedError",
    "ScreenShareError",
    # Setup
    "setup",
    # Core functions
    "get_voice_server_info",
    "get_ice_servers",
    "create_voice_connection",
    "handle_sdp_offer",
    "handle_sdp_offer_async",
    "handle_ice_candidate",
    "disconnect_voice",
    "disconnect_voice_async",
    "get_turn_credentials",
    "start_screen_share",
    "stop_screen_share",
    "get_connection_quality",
    "update_quality_hint",
    "get_active_connections",
    "validate_media_token",
    "revoke_media_token",
    "record_activity",
    "sweep_stale_connections",
]

_manager = None
_setup_complete = False


def setup(
    voice_module: Optional[Any] = None,
    events_module: Optional[Any] = None,
    sfu_backend: str = "aiortc",
    mediasoup_url: str = "http://localhost:3000",
    mediasoup_origin: str = "https://localhost",
    janus_url: str = "http://localhost:8088/janus",
    stun_urls: Optional[List[str]] = None,
    turn_urls: Optional[List[str]] = None,
    turn_secret: str = "",
    turn_ttl: int = 86400,
    turn_username: str = "",
    turn_credential: str = "",
    acoustic_defense_config: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Initialize the signaling module.

    Args:
        voice_module: Voice module for state management
        events_module: Events module for dispatching events
        sfu_backend: SFU backend to use ("aiortc", "mediasoup-ws", "mediasoup", or "janus")
            - aiortc: Pure Python WebRTC SFU (recommended, runs in-process)
            - mediasoup-ws: WebSocket adapter for mediasoup-demo
            - mediasoup: REST API adapter for custom mediasoup servers
            - janus: REST API adapter for Janus Gateway
        mediasoup_url: Mediasoup server URL (WebSocket or REST)
        mediasoup_origin: Origin header for mediasoup-ws CORS
        janus_url: Janus API URL
        stun_urls: List of STUN server URLs
        turn_urls: List of TURN server URLs
        turn_secret: Shared secret for time-limited TURN credentials (coturn)
        turn_ttl: TURN credential TTL in seconds
        turn_username: Static TURN username (for services like metered.ca)
        turn_credential: Static TURN credential/password
        acoustic_defense_config: Optional acoustic eavesdropping defense configuration
    """
    global _manager, _setup_complete

    from .manager import SignalingManager

    _manager = SignalingManager(
        voice_module=voice_module,
        events_module=events_module,
        sfu_backend=sfu_backend,
        mediasoup_url=mediasoup_url,
        mediasoup_origin=mediasoup_origin,
        janus_url=janus_url,
        stun_urls=stun_urls or ["stun:stun.l.google.com:19302"],
        turn_urls=turn_urls or [],
        turn_secret=turn_secret,
        turn_ttl=turn_ttl,
        turn_username=turn_username,
        turn_credential=turn_credential,
        acoustic_defense_config=acoustic_defense_config,
    )
    _setup_complete = True


def _get_manager():
    """Get the manager instance, raising if not setup."""
    if not _setup_complete or _manager is None:
        raise RuntimeError(
            "Signaling module not initialized. Call signaling.setup() first."
        )
    return _manager


def get_voice_server_info(user_id: int, channel_id: int) -> VoiceServerInfo:
    """Get voice server connection info including TURN credentials."""
    return _get_manager().get_voice_server_info(user_id, channel_id)


def get_ice_servers(user_id: int) -> List[ICEServer]:
    """Get the configured ICE server list for a user (no session minted)."""
    return _get_manager().get_ice_servers(user_id)


def create_voice_connection(user_id: int, channel_id: int) -> VoiceServerInfo:
    """Create a new voice connection for a user in a channel."""
    return _get_manager().create_voice_connection(user_id, channel_id)


def handle_sdp_offer(
    user_id: int,
    channel_id: int,
    sdp: str,
    sdp_type: str = "offer",
) -> SDPMessage:
    """Handle an SDP offer from a client and return the answer (sync version)."""
    return _get_manager().handle_sdp_offer(user_id, channel_id, sdp, sdp_type)


async def handle_sdp_offer_async(
    user_id: int,
    channel_id: int,
    sdp: str,
    sdp_type: str = "offer",
) -> SDPMessage:
    """Handle an SDP offer from a client and return the answer (async version that uses SFU)."""
    return await _get_manager().handle_sdp_offer_async(
        user_id, channel_id, sdp, sdp_type
    )


def handle_ice_candidate(
    user_id: int,
    channel_id: int,
    candidate: str,
    sdp_mid: Optional[str] = None,
    sdp_mline_index: Optional[int] = None,
) -> bool:
    """Handle an ICE candidate from a client."""
    return _get_manager().handle_ice_candidate(
        user_id, channel_id, candidate, sdp_mid, sdp_mline_index
    )


def disconnect_voice(user_id: int, channel_id: Optional[int] = None) -> bool:
    """Disconnect a user from voice (sync version)."""
    return _get_manager().disconnect_voice(user_id, channel_id)


async def disconnect_voice_async(
    user_id: int, channel_id: Optional[int] = None
) -> bool:
    """Disconnect a user from voice (async version that cleans up SFU)."""
    return await _get_manager().disconnect_voice_async(user_id, channel_id)


def get_turn_credentials(user_id: int) -> Optional[TURNCredentials]:
    """Get TURN server credentials for a user."""
    return _get_manager().get_turn_credentials(user_id)


def start_screen_share(user_id: int, channel_id: int) -> ScreenShareState:
    """Start screen sharing for a user."""
    return _get_manager().start_screen_share(user_id, channel_id)


def stop_screen_share(user_id: int, channel_id: int) -> bool:
    """Stop screen sharing for a user."""
    return _get_manager().stop_screen_share(user_id, channel_id)


def get_connection_quality(user_id: int, channel_id: int) -> ConnectionQuality:
    """Get connection quality metrics for a user."""
    return _get_manager().get_connection_quality(user_id, channel_id)


def update_quality_hint(
    user_id: int,
    channel_id: int,
    target_bitrate: Optional[int] = None,
    quality_level: Optional[str] = None,
    **metrics: Any,
) -> bool:
    """Update quality hints / metrics for a connection.

    Args:
        user_id: User ID (mapped to peer_id internally)
        channel_id: Voice channel ID
        target_bitrate: Target bitrate in bps (legacy)
        quality_level: Quality level name (legacy)
        **metrics: Additional metrics (bitrate, packet_loss, jitter,
                   round_trip_time, score)
    """
    m = _get_manager()
    kwargs: Dict[str, Any] = {}
    if target_bitrate is not None:
        kwargs["target_bitrate"] = target_bitrate
    if quality_level is not None:
        kwargs["quality_level"] = quality_level
    kwargs.update(metrics)
    return m.update_quality_hint(channel_id, user_id, **kwargs)


def get_quality_stats(channel_id: int) -> list:
    """Get per-peer quality stats for a channel."""
    return _get_manager().get_quality_stats(channel_id)


def get_active_connections(channel_id: int) -> List[Dict[str, Any]]:
    """Get all active connections in a channel."""
    return _get_manager().get_active_connections(channel_id)


def validate_media_token(
    token: Optional[str],
    user_id: Optional[int] = None,
    channel_id: Optional[int] = None,
) -> bool:
    """Verify a media-plane bearer token (Agent 34 C4).

    Media-plane entry points that are NOT already gateway-authenticated
    (e.g. the mediasoup_ws endpoint URL handed to clients) must verify the
    token issued with VoiceServerInfo before admitting the caller.
    """
    return _get_manager().validate_media_token(token, user_id, channel_id)


def revoke_media_token(token: Optional[str]) -> bool:
    """Revoke a previously issued media-plane bearer token."""
    return _get_manager().revoke_media_token(token)


def record_activity(user_id: int) -> bool:
    """Refresh last_activity for a user's signaling connection.

    Called on SFU stats/keepalive traffic so quiet-but-active calls are
    not falsely swept as stale.
    """
    return _get_manager().record_activity(user_id)


async def sweep_stale_connections(max_idle_ms: int = 30 * 60 * 1000) -> List[int]:
    """Disconnect signaling connections idle beyond ``max_idle_ms``.

    Agent 34 H2: performs full teardown (SFU leave + local cleanup + token
    revoke) for orphaned voice sessions; call periodically from a
    maintenance task or heartbeat loop.
    """
    return await _get_manager().sweep_stale_connections(max_idle_ms)


async def close() -> None:
    """Release resources held by the signaling module.

    Closes the SignalingManager (and any SFU adapter HTTP sessions) so
    that garbage collection does not trigger ResourceWarning.
    """
    global _manager, _setup_complete
    if _manager is not None:
        try:
            await _manager.close()
        except Exception:
            pass
        _manager = None
    _setup_complete = False
