"""
SFU adapters - Selective Forwarding Unit integrations.

Provides adapters for aiortc, mediasoup, and Janus SFU servers.

Supported backends:
- aiortc: Pure Python WebRTC SFU (recommended, runs in-process)
- mediasoup-ws: WebSocket-based adapter for mediasoup-demo server
- mediasoup: REST API adapter for custom mediasoup servers
- janus: REST API adapter for Janus Gateway
"""

from .base import SFUAdapter, SFUTransport, SFUProducer, SFUConsumer, RoomInfo
from .aiortc_adapter import AiortcAdapter
from .mediasoup import MediasoupAdapter
from .mediasoup_ws import MediasoupWSAdapter
from .janus import JanusAdapter

__all__ = [
    "SFUAdapter",
    "SFUTransport",
    "SFUProducer",
    "SFUConsumer",
    "RoomInfo",
    "AiortcAdapter",
    "MediasoupAdapter",
    "MediasoupWSAdapter",
    "JanusAdapter",
    "create_adapter",
]


def create_adapter(backend: str, **kwargs) -> SFUAdapter:
    """
    Create an SFU adapter for the specified backend.

    Args:
        backend: SFU backend name
            - "aiortc": Pure Python WebRTC SFU (recommended, runs in-process)
            - "mediasoup-ws": WebSocket adapter for mediasoup-demo
            - "mediasoup": REST API adapter for custom mediasoup servers
            - "janus": REST API adapter for Janus Gateway
        **kwargs: Backend-specific configuration
            - api_url: Server URL (REST backends)
            - ws_url: WebSocket URL (mediasoup-ws)
            - origin: Origin header for CORS (mediasoup-ws)
            - timeout: Request timeout in seconds
            - ice_servers: List of STUN/TURN server URLs (aiortc)

    Returns:
        SFUAdapter instance
    """
    if backend == "aiortc":
        return AiortcAdapter(
            ice_servers=kwargs.get("ice_servers"),
        )
    elif backend == "mediasoup-ws":
        return MediasoupWSAdapter(
            ws_url=kwargs.get("ws_url", kwargs.get("api_url", "wss://localhost:4443")),
            timeout=kwargs.get("timeout", 10),
            origin=kwargs.get("origin", "https://localhost"),
        )
    elif backend == "mediasoup":
        return MediasoupAdapter(
            api_url=kwargs.get("api_url", "http://localhost:3000"),
            timeout=kwargs.get("timeout", 10),
        )
    elif backend == "janus":
        return JanusAdapter(
            api_url=kwargs.get("api_url", "http://localhost:8088/janus"),
            timeout=kwargs.get("timeout", 10),
        )
    else:
        raise ValueError(
            f"Unknown SFU backend: {backend}. Supported: aiortc, mediasoup-ws, mediasoup, janus"
        )
