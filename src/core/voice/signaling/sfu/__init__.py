"""
SFU adapters - Selective Forwarding Unit integrations.

Provides adapters for mediasoup and Janus SFU servers.

Supported backends:
- mediasoup-ws: WebSocket-based adapter for mediasoup-demo server (recommended)
- mediasoup: REST API adapter for custom mediasoup servers
- janus: REST API adapter for Janus Gateway
"""

from .base import SFUAdapter, SFUTransport, SFUProducer, SFUConsumer, RoomInfo
from .mediasoup import MediasoupAdapter
from .mediasoup_ws import MediasoupWSAdapter
from .janus import JanusAdapter

__all__ = [
    "SFUAdapter",
    "SFUTransport",
    "SFUProducer",
    "SFUConsumer",
    "RoomInfo",
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
            - "mediasoup-ws": WebSocket adapter for mediasoup-demo (recommended)
            - "mediasoup": REST API adapter for custom mediasoup servers
            - "janus": REST API adapter for Janus Gateway
        **kwargs: Backend-specific configuration
            - api_url: Server URL (REST backends)
            - ws_url: WebSocket URL (mediasoup-ws)
            - origin: Origin header for CORS (mediasoup-ws)
            - timeout: Request timeout in seconds
        
    Returns:
        SFUAdapter instance
    """
    if backend == "mediasoup-ws":
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
        raise ValueError(f"Unknown SFU backend: {backend}. Supported: mediasoup-ws, mediasoup, janus")
