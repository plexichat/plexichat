"""
Mediasoup SFU adapter - Integration with mediasoup-based SFU servers.

Makes actual HTTP requests to the mediasoup API.
"""

import asyncio
import os
import time
from typing import Dict, List, Optional, Any

import utils.logger as logger

from ..exceptions import SFUConnectionError, SFUTimeoutError
from .base import (
    SFUAdapter,
    SFUTransport,
    SFUProducer,
    SFUConsumer,
    RoomInfo,
    TransportDirection,
    MediaKind,
    AcousticDefenseConfig,
)


class MediasoupAdapter(SFUAdapter):
    """
    Adapter for mediasoup-based SFU servers.

    Expects a mediasoup server with a REST API following the common
    mediasoup-demo patterns.
    """

    def __init__(
        self,
        api_url: str,
        timeout: int = 10,
        acoustic_defense: Optional[AcousticDefenseConfig] = None,
    ):
        """
        Initialize the mediasoup adapter.

        Args:
            api_url: Base URL of the mediasoup API
            timeout: Request timeout in seconds
            acoustic_defense: Optional acoustic eavesdropping defense configuration
        """
        self._api_url = api_url.rstrip("/")
        self._timeout = timeout
        self._session = None
        self._recordings: Dict[str, Dict] = {}
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

    async def _get_session(self):
        """Get or create aiohttp session."""
        if self._session is None:
            try:
                import aiohttp

                self._session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self._timeout)
                )
            except ImportError:
                raise SFUConnectionError(
                    "aiohttp is required for mediasoup adapter",
                    backend="mediasoup",
                    url=self._api_url,
                )
        return self._session

    async def _request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Make an HTTP request to the mediasoup API.

        Args:
            method: HTTP method
            endpoint: API endpoint
            data: Request body data

        Returns:
            Response JSON
        """
        session = await self._get_session()
        url = f"{self._api_url}{endpoint}"

        try:
            async with session.request(
                method,
                url,
                json=data,
                headers={"Content-Type": "application/json"},
            ) as response:
                if response.status >= 400:
                    text = await response.text()
                    raise SFUConnectionError(
                        f"Mediasoup API error: {response.status} - {text}",
                        backend="mediasoup",
                        url=url,
                    )

                if response.content_type == "application/json":
                    return await response.json()
                return {}

        except asyncio.TimeoutError:
            raise SFUTimeoutError(
                "Mediasoup request timed out",
                operation=endpoint,
                timeout_ms=self._timeout * 1000,
            )
        except Exception as e:
            if isinstance(e, (SFUConnectionError, SFUTimeoutError)):
                raise
            raise SFUConnectionError(
                f"Mediasoup connection failed: {e}", backend="mediasoup", url=url
            )

    async def create_room(self, room_id: str) -> RoomInfo:
        """Create a new room on the mediasoup server."""
        result = await self._request(
            "POST",
            "/rooms",
            {
                "roomId": room_id,
                "acoustic_defense": self._acoustic_defense_to_dict(),
            },
        )

        logger.debug(f"Created mediasoup room: {room_id}")

        return RoomInfo(
            id=room_id,
            peers=result.get("peers", []),
            producers=result.get("producers", []),
        )

    async def close_room(self, room_id: str) -> bool:
        """Close a room on the mediasoup server."""
        await self._request("DELETE", f"/rooms/{room_id}")
        logger.debug(f"Closed mediasoup room: {room_id}")
        return True

    async def join_room(self, room_id: str, peer_id: str) -> Dict[str, Any]:
        """Join a peer to a room."""
        result = await self._request(
            "POST",
            f"/rooms/{room_id}/peers",
            {
                "peerId": peer_id,
                "acoustic_defense": self._acoustic_defense_to_dict(),
            },
        )

        logger.debug(f"Peer {peer_id} joined room {room_id}")

        return {
            "routerRtpCapabilities": result.get("routerRtpCapabilities", {}),
            "peers": result.get("peers", []),
            "producers": result.get("producers", []),
        }

    async def leave_room(self, room_id: str, peer_id: str) -> bool:
        """Remove a peer from a room."""
        await self._request("DELETE", f"/rooms/{room_id}/peers/{peer_id}")
        logger.debug(f"Peer {peer_id} left room {room_id}")
        return True

    async def create_transport(
        self,
        room_id: str,
        peer_id: str,
        direction: TransportDirection,
    ) -> SFUTransport:
        """Create a WebRTC transport for a peer."""
        result = await self._request(
            "POST",
            f"/rooms/{room_id}/peers/{peer_id}/transports",
            {
                "direction": direction.value,
                "sctpCapabilities": None,
            },
        )

        transport = SFUTransport(
            id=result["id"],
            direction=direction,
            ice_parameters=result.get("iceParameters", {}),
            ice_candidates=result.get("iceCandidates", []),
            dtls_parameters=result.get("dtlsParameters", {}),
            sctp_parameters=result.get("sctpParameters"),
        )

        logger.debug(f"Created transport {transport.id} for peer {peer_id}")

        return transport

    async def connect_transport(
        self,
        room_id: str,
        peer_id: str,
        transport_id: str,
        dtls_parameters: Dict[str, Any],
    ) -> bool:
        """Connect a transport with DTLS parameters."""
        await self._request(
            "POST",
            f"/rooms/{room_id}/peers/{peer_id}/transports/{transport_id}/connect",
            {"dtlsParameters": dtls_parameters},
        )

        logger.debug(f"Connected transport {transport_id}")
        return True

    async def produce(
        self,
        room_id: str,
        peer_id: str,
        transport_id: str,
        kind: MediaKind,
        rtp_parameters: Dict[str, Any],
    ) -> SFUProducer:
        """Create a producer to send media."""
        result = await self._request(
            "POST",
            f"/rooms/{room_id}/peers/{peer_id}/transports/{transport_id}/produce",
            {
                "kind": kind.value,
                "rtpParameters": rtp_parameters,
            },
        )

        producer = SFUProducer(
            id=result["id"],
            kind=kind,
            rtp_parameters=rtp_parameters,
            paused=result.get("paused", False),
        )

        logger.debug(f"Created producer {producer.id} ({kind.value})")

        return producer

    async def consume(
        self,
        room_id: str,
        peer_id: str,
        transport_id: str,
        producer_id: str,
        rtp_capabilities: Dict[str, Any],
    ) -> SFUConsumer:
        """Create a consumer to receive media."""
        result = await self._request(
            "POST",
            f"/rooms/{room_id}/peers/{peer_id}/transports/{transport_id}/consume",
            {
                "producerId": producer_id,
                "rtpCapabilities": rtp_capabilities,
            },
        )

        consumer = SFUConsumer(
            id=result["id"],
            producer_id=producer_id,
            kind=MediaKind(result["kind"]),
            rtp_parameters=result.get("rtpParameters", {}),
            paused=result.get("paused", False),
        )

        logger.debug(f"Created consumer {consumer.id} for producer {producer_id}")

        return consumer

    async def pause_producer(
        self,
        room_id: str,
        peer_id: str,
        producer_id: str,
    ) -> bool:
        """Pause a producer."""
        await self._request(
            "POST",
            f"/rooms/{room_id}/peers/{peer_id}/producers/{producer_id}/pause",
        )
        return True

    async def resume_producer(
        self,
        room_id: str,
        peer_id: str,
        producer_id: str,
    ) -> bool:
        """Resume a producer."""
        await self._request(
            "POST",
            f"/rooms/{room_id}/peers/{peer_id}/producers/{producer_id}/resume",
        )
        return True

    async def close_producer(
        self,
        room_id: str,
        peer_id: str,
        producer_id: str,
    ) -> bool:
        """Close a producer."""
        await self._request(
            "DELETE",
            f"/rooms/{room_id}/peers/{peer_id}/producers/{producer_id}",
        )
        return True

    async def get_room_info(self, room_id: str) -> Optional[RoomInfo]:
        """Get information about a room."""
        try:
            result = await self._request("GET", f"/rooms/{room_id}")
            return RoomInfo(
                id=room_id,
                peers=result.get("peers", []),
                producers=result.get("producers", []),
            )
        except SFUConnectionError:
            return None

    async def get_router_capabilities(self, room_id: str) -> Dict[str, Any]:
        """Get RTP capabilities for a room's router."""
        result = await self._request("GET", f"/rooms/{room_id}/routerCapabilities")
        return result.get("rtpCapabilities", {})

    async def set_preferred_layers(
        self,
        room_id: str,
        peer_id: str,
        consumer_id: str,
        spatial_layer: int,
        temporal_layer: int,
    ) -> bool:
        """Set preferred simulcast layers for a consumer."""
        await self._request(
            "POST",
            f"/rooms/{room_id}/peers/{peer_id}/consumers/{consumer_id}/preferredLayers",
            {
                "spatialLayer": spatial_layer,
                "temporalLayer": temporal_layer,
            },
        )
        return True

    async def start_recording(self, room_id: str, output_dir: str) -> Dict[str, Any]:
        """Start recording by creating a recorder transport on the room."""
        recording_id = f"rec_{room_id}_{int(time.time())}"
        try:
            result = await self._request(
                "POST",
                f"/rooms/{room_id}/recording/start",
                {
                    "outputDir": output_dir,
                    "recordingId": recording_id,
                    "acoustic_defense": self._acoustic_defense_to_dict(),
                },
            )
        except SFUConnectionError:
            logger.warning(
                f"Mediasoup REST API does not support recording endpoints; "
                f"recording for room {room_id} will be tracked locally"
            )
            result = {}

        file_count = result.get("file_count", 0)
        self._recordings[room_id] = {
            "recording_id": recording_id,
            "output_dir": output_dir,
            "file_count": file_count,
        }
        logger.info(
            f"Recording started for room {room_id} (id={recording_id}, files={file_count})"
        )
        return {"recording_id": recording_id, "file_count": file_count}

    async def stop_recording(self, room_id: str) -> Optional[List[str]]:
        """Stop recording and return file paths."""
        info = self._recordings.pop(room_id, None)
        if not info:
            logger.info(
                f"Stop recording called for room {room_id} but no active recording found"
            )
            return None

        try:
            result = await self._request(
                "POST",
                f"/rooms/{room_id}/recording/stop",
                {"recordingId": info["recording_id"]},
            )
            files: List[str] = result.get("files", [])
            if files:
                logger.info(f"Recording stopped for room {room_id}, files={len(files)}")
                return files
        except SFUConnectionError:
            logger.warning(
                f"Mediasoup REST API does not support recording stop; "
                f"recording for room {room_id} was tracked locally only"
            )

        return [os.path.join(info["output_dir"], f"{info['recording_id']}.webm")]

    async def health_check(self) -> bool:
        """Check if the mediasoup server is healthy."""
        try:
            await self._request("GET", "/health")
            return True
        except Exception:
            return False

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None
