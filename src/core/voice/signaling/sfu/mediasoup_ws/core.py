"""Shared state and helpers for the mediasoup WebSocket (protoo) adapter.

The adapter is composed from one mixin per responsibility (connection, rooms,
recording). This module owns the state those mixins share, plus the
cross-mixin method contract they call through ``self``, so no mixin has to
import another one.
"""

import asyncio
import ssl
from typing import Any, Callable, Dict, List, Optional

import utils.logger as logger

from ..base import (
    AcousticDefenseConfig,
    RoomInfo,
    SFUAdapter,
    SFUConsumer,
    SFUTransport,
    TransportDirection,
)
from .models import PeerConnection


class MediasoupWSAdapterCore(SFUAdapter):
    """Adapter state, TLS setup, and the behaviour contract."""

    def __init__(
        self,
        ws_url: str,
        timeout: int = 10,
        origin: str = "https://localhost:4443",
        acoustic_defense: Optional[AcousticDefenseConfig] = None,
    ):
        """
        Initialize the mediasoup WebSocket adapter.

        Args:
            ws_url: WebSocket URL of the mediasoup server (e.g., wss://host:4443)
            timeout: Request timeout in seconds
            origin: Origin header to send (must match server config)
            acoustic_defense: Optional acoustic eavesdropping defense configuration
        """
        self._ws_url = ws_url.rstrip("/")
        self._timeout = timeout
        self._origin = origin
        self._connections: Dict[str, PeerConnection] = {}  # peer_id -> connection
        self._rooms: Dict[str, RoomInfo] = {}  # room_id -> info
        self._recordings: Dict[str, Dict] = {}  # room_id -> recording state
        # Recording teardown failures encountered during final adapter shutdown
        # are retained as explicit diagnostics, not as retryable recordings:
        # shutdown closes the signaling connection and cannot retry afterward.
        self._recording_teardown_failures: Dict[str, Dict[str, Any]] = {}
        self._quality_mixin: Optional[Any] = None
        self._message_handlers: Dict[str, Callable] = {}
        # Strong references to the per-connection receive tasks: asyncio only
        # holds a weak reference, so a task created without one can be
        # garbage-collected mid-flight and silently end the SFU session.
        self._receiver_tasks: Dict[str, asyncio.Task] = {}
        self._ssl_context = self._create_ssl_context()
        self._acoustic_defense = acoustic_defense

    def _acoustic_defense_to_dict(self) -> Dict[str, Any]:
        if not self._acoustic_defense or not self._acoustic_defense.enabled:
            return {}
        return {
            "enabled": self._acoustic_defense.enabled,
            "jitter_ms_min": self._acoustic_defense.jitter_ms_min,
            "jitter_ms_max": self._acoustic_defense.jitter_ms_max,
            "spectral_masking": self._acoustic_defense.spectral_masking,
            "spectral_mask_noise_db": self._acoustic_defense.spectral_mask_noise_db,
            "spectral_mask_low_hz": self._acoustic_defense.spectral_mask_low_hz,
            "spectral_mask_high_hz": self._acoustic_defense.spectral_mask_high_hz,
            "vad_gating": self._acoustic_defense.vad_gating,
            "vad_speech_threshold": self._acoustic_defense.vad_speech_threshold,
            "vad_silence_frames": self._acoustic_defense.vad_silence_frames,
            "transient_shaving": self._acoustic_defense.transient_shaving,
            "transient_attack_ms": self._acoustic_defense.transient_attack_ms,
            "transient_release_ms": self._acoustic_defense.transient_release_ms,
            "transient_ratio": self._acoustic_defense.transient_ratio,
        }

    def set_quality_mixin(self, quality_mixin: Any) -> None:
        """Set the quality mixin reference for score notifications."""
        self._quality_mixin = quality_mixin

    def _create_ssl_context(self) -> ssl.SSLContext:
        """Create an SSL context for the mediasoup signaling socket.

        SECURITY: the previous implementation set
        ``verify_mode=ssl.CERT_NONE`` and ``check_hostname=False`` which
        disabled every protection against an active MITM. Any peer on
        the wire path could substitute / replay packets and our
        WebRTC handshake (carrying ICE / DTLS fingerprints) would still
        accept the malicious server.

        We now require full chain + hostname verification by default.
        Operators who actually need self-signed dev certs must opt in
        explicitly via ``voice.mediated.mtls.allow_self_signed`` (only
        honourable in ``MAINTAINER_MODE``/dev); operators who run
        production with a public CA MUST NOT touch that flag.
        """
        import os as _os
        import utils.config as _cfg

        ctx = ssl.create_default_context()
        try:
            voice_cfg = _cfg.get("voice", {}) or {}
            mediated = voice_cfg.get("mediated", {}) or {}
            mtls_cfg = mediated.get("mtls", {}) or {}
            allow_self_signed = bool(mtls_cfg.get("allow_self_signed", False))
        except Exception:
            allow_self_signed = False

        # Refuse self-signed overrides outside an explicit dev mode.
        # ``PLEXICHAT_ALLOW_INSECURE_TLS=1`` is the documented opt-in
        # but is intentionally noisy: any operator running it in
        # production sees the loud CRITICAL log line below.
        env_override = _os.environ.get("PLEXICHAT_ALLOW_INSECURE_TLS", "").lower() in {
            "1",
            "true",
            "yes",
        }
        env_override = env_override and not _os.environ.get(
            "PLEXICHAT_REQUIRE_FAIL_CLOSED", ""
        )

        if allow_self_signed and env_override:
            logger.critical(
                "mediasoup WebSocket TLS verification DISABLED via "
                "PLEXICHAT_ALLOW_INSECURE_TLS + voice.mediated.mtls."
                "allow_self_signed. This MUST NOT be used in "
                "production."
            )
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx

        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx

    def _get_connection_key(self, room_id: str, peer_id: str) -> str:
        """Get unique key for a peer connection."""
        return f"{room_id}:{peer_id}"

    def _report_peer_quality(
        self,
        connection: PeerConnection,
        notification_data: Dict[str, Any],
        **metrics: Any,
    ) -> None:
        """Report an SFU score notification to the canonical quality store.

        ``producerScore`` / ``consumerScore`` carry SFU-scoped identifiers
        (``voice_<channel>`` / ``user_<id>``); the shared base bridge maps them
        back to the numeric ids the store is keyed by and contains any sink
        failure, so a metric update can never tear down the receive loop.
        """
        raw_peer = notification_data.get("peerId") or connection.peer_id
        self.report_quality(connection.room_id, raw_peer, metrics)

    def register_consumer_handler(
        self,
        room_id: str,
        peer_id: str,
        handler: Callable,
    ):
        """Register a handler for new consumers."""
        key = f"newConsumer:{room_id}:{peer_id}"
        self._message_handlers[key] = handler

    def register_active_speaker_handler(
        self,
        room_id: str,
        handler: Callable,
    ):
        """Register a handler for active speaker changes."""
        key = f"activeSpeaker:{room_id}"
        self._message_handlers[key] = handler

    # -- Implemented by the behaviour mixins ------------------------------
    async def _connect(self, room_id: str, peer_id: str) -> PeerConnection:
        """Connect a peer to ``room_id`` (implemented by the connection mixin)."""
        raise NotImplementedError

    async def _request(
        self,
        connection: PeerConnection,
        method: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Send a protoo request (implemented by the connection mixin)."""
        raise NotImplementedError

    async def create_transport(
        self,
        room_id: str,
        peer_id: str,
        direction: TransportDirection,
    ) -> SFUTransport:
        """Create a transport (implemented by the rooms mixin)."""
        raise NotImplementedError

    async def consume(
        self,
        room_id: str,
        peer_id: str,
        transport_id: str,
        producer_id: str,
        rtp_capabilities: Dict[str, Any],
    ) -> SFUConsumer:
        """Find a consumer (implemented by the rooms mixin)."""
        raise NotImplementedError

    async def leave_room(self, room_id: str, peer_id: str) -> bool:
        """Remove a peer from a room (implemented by the rooms mixin)."""
        raise NotImplementedError

    async def start_recording(self, room_id: str, output_dir: str) -> Dict[str, Any]:
        """Start a recording (implemented by the recording mixin)."""
        raise NotImplementedError

    async def stop_recording(self, room_id: str) -> Optional[List[str]]:
        """Stop a recording (implemented by the recording mixin)."""
        raise NotImplementedError

    def _record_recording_teardown_failure(self, room_id: str, reason: str) -> None:
        """Record unrecoverable cleanup (implemented by the recording mixin)."""
        raise NotImplementedError
