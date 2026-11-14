"""aiortc SFU adapter: the composition root.

The implementation lives in one mixin per concern:

* :mod:`.senders` -- fan-out relays and sender hygiene
* :mod:`.negotiation` -- offers, answers, ICE and SFU renegotiation
* :mod:`.transports` -- transports and DTLS parameters
* :mod:`.media_ops` -- producers, consumers and forwarding
* :mod:`.degradation` -- layer hints as bounded video frame rates
* :mod:`.quality_bridge` -- aiortc stats into quality samples
* :mod:`.recording` -- room recording and auto-recording of arriving tracks
* :mod:`.rooms` -- room and peer lifecycle

``AiortcAdapter`` composes them, so ``from ...aiortc_adapter import
AiortcAdapter`` and every public method keep working unchanged. A mixin that
uses another mixin's method declares that call itself; the implementing mixin
comes first in the bases, so the MRO resolves to the real method at runtime
and pyright type-checks the call.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import utils.logger as logger

from ...exceptions import SFUConnectionError
from ..base import AcousticDefenseConfig, SFUAdapter
from ..fanout import TrackFanout
from ..quality import QualityReporter
from .degradation import DegradationMixin
from .media_ops import MediaOpsMixin
from .models import AiortcPeer
from .negotiation import NegotiationMixin
from .quality_bridge import QualityBridgeMixin
from .recording import RecordingMixin
from .rooms import RoomsMixin
from .runtime import _aiortc_available, _aiortc_recording_available
from .runtime import _total_bytes_sent as _total_bytes_sent
from .senders import SenderMixin
from .transports import TransportsMixin


class AiortcAdapter(
    SenderMixin,
    NegotiationMixin,
    TransportsMixin,
    MediaOpsMixin,
    DegradationMixin,
    RecordingMixin,
    RoomsMixin,
    QualityBridgeMixin,
    SFUAdapter,
):
    """
    aiortc-based SFU adapter.

    This adapter implements an SFU using aiortc directly in Python.
    It manages peer connections and routes media streams between peers.
    """

    _rooms: Dict[str, Dict[str, AiortcPeer]]
    _ice_servers: List[Dict[str, Any]]
    _acoustic_defense: Any
    _renegotiation_sink: Any
    _fanouts: Dict[Any, TrackFanout]
    _recordings: Dict[str, Dict[str, Any]]
    _quality_reporter: Optional[QualityReporter]
    _last_quality_sample: Dict[str, Tuple[int, float]]

    def __init__(
        self,
        ice_servers: Optional[List[Dict[str, Any]]] = None,
        acoustic_defense: Optional[AcousticDefenseConfig] = None,
    ):
        """
        Initialize the aiortc SFU adapter.

        Args:
            ice_servers: List of STUN/TURN server configurations in aiortc format
                [{"urls": "stun:..."}, {"urls": "turn:...", "username": "...", "credential": "..."}]
            acoustic_defense: Optional acoustic eavesdropping defense configuration
        """
        if not _aiortc_available():
            raise SFUConnectionError(
                "aiortc is not installed. Install with: pip install aiortc==1.14.0"
            )

        self._rooms = {}
        self._ice_servers = ice_servers or []
        self._acoustic_defense = acoustic_defense or AcousticDefenseConfig()
        # Deliverer of an SFU-initiated offer (see set_renegotiation_sink).
        self._renegotiation_sink = None
        # One read-once relay per produced track that is forwarded to several
        # peers, so every sender receives every frame instead of a fraction.
        self._fanouts = {}
        self._recordings = {}
        # Quality bridge state (see ``collect_quality``).
        self._quality_reporter = None
        # Last ``(bytesSent, monotonic ts)`` per peer, for bitrate deltas.
        self._last_quality_sample = {}
        if _aiortc_recording_available():
            logger.info("aiortc MediaRecorder available for call recording")
        else:
            logger.info("aiortc MediaRecorder not available - call recording disabled")
        logger.info("aiortc SFU adapter initialized")

    def set_quality_mixin(self, quality_mixin: Any) -> None:
        """Wire the quality sink and start polling aiortc connection stats.

        aiortc never pushes scores, so quality is pulled from
        ``RTCPeerConnection.getStats()``. Without this the default in-process
        backend reported nothing at all and BandwidthEstimator had no input,
        the same gap the janus and mediasoup-REST adapters had.
        """
        super().set_quality_mixin(quality_mixin)
        if self._quality_reporter is None:
            self._quality_reporter = QualityReporter(self)
        self._quality_reporter.start()

    async def health_check(self) -> bool:
        """Check if the SFU server is healthy."""
        return _aiortc_available()

    async def close(self) -> None:
        """Close the adapter and clean up all resources."""
        if self._quality_reporter is not None:
            self._quality_reporter.stop()
            self._quality_reporter = None
        self._last_quality_sample.clear()

        for room_id in list(self._recordings.keys()):
            await self.stop_recording(room_id)

        for room_id in list(self._rooms.keys()):
            await self.close_room(room_id)

        for relay in list(self._fanouts.values()):
            await relay.aclose()
        self._fanouts.clear()

        logger.info("aiortc SFU adapter closed")
