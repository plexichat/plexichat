"""Data models for the aiortc SFU adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from ..base import SFUConsumer, SFUProducer, SFUTransport


@dataclass
class AiortcPeer:
    """A peer in the aiortc SFU."""

    peer_id: str
    pc: Any
    producers: Dict[str, SFUProducer] = field(default_factory=dict)
    consumers: Dict[str, SFUConsumer] = field(default_factory=dict)
    transports: Dict[str, SFUTransport] = field(default_factory=dict)
    incoming_audio_tracks: List[Any] = field(default_factory=list)
    incoming_video_tracks: List[Any] = field(default_factory=list)
    producer_tracks: Dict[str, Any] = field(default_factory=dict)
    # The room this peer belongs to, so a forwarded track can name its target
    # when an offer has to be delivered back to the client.
    room_id: str = ""
    # Senders whose outbound video a degradation hint replaced with a
    # frame-rate bounded view, mapped back to the source track that must be
    # re-attached to restore full rate.
    degraded_senders: Dict[Any, Any] = field(default_factory=dict)


__all__ = ["AiortcPeer"]
