"""aiortc-based SFU adapter (package).

Compatibility shim: preserves the public and test-patched surface of the former
``aiortc_adapter.py`` module so existing imports and monkeypatch points keep
working (``from ...aiortc_adapter import AiortcAdapter``,
``import ...aiortc_adapter as mod; mod.RTCPeerConnection = ...``).
"""

from __future__ import annotations

import importlib
import time

# Optional aiortc deps are resolved at import time and exposed as patchable
# module attributes. The adapter resolves them from this package module at call
# time so test monkeypatches on ``aiortc_adapter.RTCPeerConnection`` /
# ``MediaRecorder`` / availability flags take effect.
from ._deps import (
    AIORTC_AVAILABLE,
    AIORTC_RECORDING_AVAILABLE,
    MediaRecorder,
    RTCPeerConnection,
)

from ..acoustic_defense import AudioProcessingTrack as AudioProcessingTrack
from .adapter import (
    AiortcAdapter as AiortcAdapter,
    _total_bytes_sent as _total_bytes_sent,
)
from .constants import (
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
from .errors import SFUValidationError as SFUValidationError
from .media import (
    BoundedVideoTrack as BoundedVideoTrack,
    _same_sender_track as _same_sender_track,
)
from .models import AiortcPeer as AiortcPeer
from .sdp import (
    _parse_dtls_params_from_sdp as _parse_dtls_params_from_sdp,
    _parse_ice_candidates_from_sdp as _parse_ice_candidates_from_sdp,
    _parse_ice_params_from_sdp as _parse_ice_params_from_sdp,
)
from .stats import (
    _as_number as _as_number,
    _quality_metrics_from_stats as _quality_metrics_from_stats,
)
from .validation import (
    validate_ice_candidate_payload as validate_ice_candidate_payload,
    validate_identifier as validate_identifier,
    validate_payload_mapping as validate_payload_mapping,
    validate_recording_output_dir as validate_recording_output_dir,
    validate_sdp_payload as validate_sdp_payload,
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
