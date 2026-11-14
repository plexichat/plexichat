"""Patchable aiortc bindings shared by the adapter mixins.

The package module (``aiortc_adapter``) re-exports the optional aiortc names
and a ``time`` module so tests can patch ``aiortc_adapter.RTCPeerConnection``,
``aiortc_adapter.MediaRecorder``, ``aiortc_adapter.AudioProcessingTrack`` and
``aiortc_adapter.time``. Resolving each name from the package at *call* time is
what keeps those patch points live now that the implementation is split by
concern instead of held in one module.
"""

from __future__ import annotations

import importlib
import sys
import time as _time_mod
from typing import Any, Optional, cast

from ...exceptions import SFUConnectionError
from ..acoustic_defense import AudioProcessingTrack
from .stats import _as_number

# The package module, so attribute lookups see later monkeypatches.
_pkg = sys.modules[__package__ or __name__.rsplit(".", 1)[0]]


def clock() -> Any:
    """The ``time`` module in use, honouring a patched package attribute."""
    return getattr(_pkg, "time", _time_mod)


def _rtc_peer_connection_cls() -> Any:
    return getattr(_pkg, "RTCPeerConnection", None)


def _media_recorder_cls() -> Any:
    return getattr(_pkg, "MediaRecorder", None)


def _aiortc_available() -> bool:
    return bool(getattr(_pkg, "AIORTC_AVAILABLE", False))


def _aiortc_recording_available() -> bool:
    return bool(getattr(_pkg, "AIORTC_RECORDING_AVAILABLE", False))


def _audio_processing_track_cls() -> Any:
    return getattr(_pkg, "AudioProcessingTrack", AudioProcessingTrack)


def _rtc_session_description(**kwargs: Any) -> Any:
    mod = cast(Any, importlib.import_module("aiortc"))
    return mod.RTCSessionDescription(**kwargs)


def _rtc_ice_candidate(**kwargs: Any) -> Any:
    mod = cast(Any, importlib.import_module("aiortc"))
    return mod.RTCIceCandidate(**kwargs)


def _total_bytes_sent(records: Any) -> Optional[int]:
    """Sum the ``outbound-rtp`` byte counters of one peer's pc, if present."""
    total = 0
    seen = False
    for record in records or ():
        if getattr(record, "type", None) != "outbound-rtp":
            continue
        value = _as_number(getattr(record, "bytesSent", None))
        if value is None or value < 0:
            continue
        total += int(value)
        seen = True
    return total if seen else None


def _make_rtc_peer_connection() -> Any:
    cls = _rtc_peer_connection_cls()
    if cls is None:
        raise SFUConnectionError("aiortc is not installed")
    return cls()


def _make_media_recorder(filepath: str) -> Any:
    cls = _media_recorder_cls()
    if cls is None:
        raise SFUConnectionError("MediaRecorder is not available")
    return cls(filepath)
