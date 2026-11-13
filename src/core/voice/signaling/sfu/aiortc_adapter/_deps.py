"""Optional aiortc dependency probes, resolved once at import time.

The adapter package re-exports these names so tests can monkeypatch
``aiortc_adapter.RTCPeerConnection`` and the adapter resolves them from
the package module at call time.
"""

from typing import Any

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
