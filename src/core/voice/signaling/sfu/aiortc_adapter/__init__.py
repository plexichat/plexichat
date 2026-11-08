"""aiortc-based SFU adapter (package).

Compatibility shim: preserves the public and test-patched surface of the former
``aiortc_adapter.py`` module so existing imports and monkeypatch points keep
working (``from ...aiortc_adapter import AiortcAdapter``,
``import ...aiortc_adapter as mod; mod.RTCPeerConnection = ...``).
"""

from __future__ import annotations

import importlib
import time
from typing import Any

# Optional aiortc deps are resolved at import time and exposed as patchable
# module attributes. The adapter resolves them from this package module at call
# time so test monkeypatches on ``aiortc_adapter.RTCPeerConnection`` /
# ``MediaRecorder`` / availability flags take effect.
RTCPeerConnection: Any = None
AIORTC_AVAILABLE = False
try:
    from aiortc import RTCPeerConnection as _RTCPeerConnection

    RTCPeerConnection = _RTCPeerConnection
    AIORTC_AVAILABLE = True
except ImportError:  # pragma: no cover
    pass

MediaRecorder: Any = None
AIORTC_RECORDING_AVAILABLE = False
try:
    from aiortc.contrib.media import MediaRecorder as _MediaRecorder

    MediaRecorder = _MediaRecorder
    AIORTC_RECORDING_AVAILABLE = True
except ImportError:  # pragma: no cover
    pass

from ..acoustic_defense import AudioProcessingTrack  # noqa: E402
from .adapter import AiortcAdapter, _total_bytes_sent  # noqa: E402
from .constants import (  # noqa: E402
    _DEFAULT_CLOCK_RATE as DEFAULT_CLOCK_RATE,
    DEFAULT_MAX_INCOMING_TRACKS,
    DEGRADED_VIDEO_FPS,
    MAX_ICE_CANDIDATE_BYTES,
    MAX_PAYLOAD_DEPTH,
    MAX_PAYLOAD_KEYS,
    MAX_ROOM_ID_LEN,
    MAX_SDP_BYTES,
    _CLOCK_RATES,
)
from .errors import SFUValidationError  # noqa: E402
from .media import BoundedVideoTrack, _same_sender_track  # noqa: E402
from .models import AiortcPeer  # noqa: E402
from .sdp import (  # noqa: E402
    _parse_dtls_params_from_sdp,
    _parse_ice_candidates_from_sdp,
    _parse_ice_params_from_sdp,
)
from .stats import _as_number, _quality_metrics_from_stats  # noqa: E402
from .validation import (  # noqa: E402
    validate_ice_candidate_payload,
    validate_identifier,
    validate_payload_mapping,
    validate_recording_output_dir,
    validate_sdp_payload,
)

__all__ = [
    "AiortcAdapter",
    "AudioProcessingTrack",
    "time",
    "AiortcPeer",
    "BoundedVideoTrack",
    "SFUValidationError",
    "RTCPeerConnection",
    "AIORTC_AVAILABLE",
    "MediaRecorder",
    "AIORTC_RECORDING_AVAILABLE",
    "DEGRADED_VIDEO_FPS",
    "DEFAULT_MAX_INCOMING_TRACKS",
    "MAX_ROOM_ID_LEN",
    "MAX_SDP_BYTES",
    "MAX_ICE_CANDIDATE_BYTES",
    "MAX_PAYLOAD_DEPTH",
    "MAX_PAYLOAD_KEYS",
    "_CLOCK_RATES",
    "DEFAULT_CLOCK_RATE",
    "_parse_ice_params_from_sdp",
    "_parse_ice_candidates_from_sdp",
    "_parse_dtls_params_from_sdp",
    "_quality_metrics_from_stats",
    "_as_number",
    "_total_bytes_sent",
    "_same_sender_track",
    "validate_identifier",
    "validate_sdp_payload",
    "validate_ice_candidate_payload",
    "validate_payload_mapping",
    "validate_recording_output_dir",
    "importlib",
]
