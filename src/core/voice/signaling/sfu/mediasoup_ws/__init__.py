"""Mediasoup WebSocket SFU adapter package.

Compatibility shim: the former ``mediasoup_ws.py`` module is now a package, and
this namespace preserves its public and test-patched surface --
``MediasoupWSAdapter``, ``PeerConnection``, ``PendingRequest``, the stable-peer
identity helpers, and the recording path guards -- so existing imports keep
working unchanged.

The recording path guards are re-exported here because tests monkeypatch
``...mediasoup_ws.ensure_within_recording_dir``; :mod:`.recording` resolves them
through this package at call time.
"""

from __future__ import annotations

import os
import time
from typing import Any

from ..base import (
    AcousticDefenseConfig as AcousticDefenseConfig,
    MediaKind as MediaKind,
    RoomInfo as RoomInfo,
    SFUAdapter as SFUAdapter,
    SFUConsumer as SFUConsumer,
    SFUProducer as SFUProducer,
    SFUTransport as SFUTransport,
    TransportDirection as TransportDirection,
)
from ..paths import (
    ensure_within_recording_dir as ensure_within_recording_dir,
    resolve_recording_dir as resolve_recording_dir,
    safe_filename_component as safe_filename_component,
)
from ...exceptions import (
    SFUConnectionError as SFUConnectionError,
    SFUTimeoutError as SFUTimeoutError,
)
from . import identity as _identity
from .adapter import MediasoupWSAdapter
from .identity import (
    _load_stable_peer_secret as _load_stable_peer_secret,
    _stable_peer_id as _stable_peer_id,
)
from .models import PendingRequest, PeerConnection

__all__ = [
    "MediasoupWSAdapter",
    "PeerConnection",
    "PendingRequest",
    "SFUAdapter",
    "SFUConnectionError",
    "SFUConsumer",
    "SFUProducer",
    "SFUTimeoutError",
    "SFUTransport",
    "AcousticDefenseConfig",
    "MediaKind",
    "RoomInfo",
    "TransportDirection",
    "_load_stable_peer_secret",
    "_stable_peer_id",
    "ensure_within_recording_dir",
    "resolve_recording_dir",
    "safe_filename_component",
    "os",
    "time",
]


def __getattr__(name: str) -> Any:
    """Expose the live stable-peer secret written by :mod:`.identity`."""
    if name == "_STABLE_PEER_SECRET":
        return _identity._STABLE_PEER_SECRET
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
