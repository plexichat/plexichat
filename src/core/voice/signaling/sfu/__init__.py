"""
SFU adapters - Selective Forwarding Unit integrations.

Provides adapters for aiortc, mediasoup, and Janus SFU servers.

Supported backends:
- aiortc: Pure Python WebRTC SFU (recommended, runs in-process)
- mediasoup-ws: WebSocket-based adapter for mediasoup-demo server
- mediasoup: REST API adapter for custom mediasoup servers
- janus: REST API adapter for Janus Gateway
"""

from .base import (
    SFUAdapter,
    SFUTransport,
    SFUProducer,
    SFUConsumer,
    RoomInfo,
    AcousticDefenseConfig,
)
from .acoustic_defense import (
    AudioProcessingTrack,
)

# Adapters are imported lazily. Pulling in aiortc, mediasoup and janus at
# module import time costs roughly a second of startup for a feature many
# installs never use. ``from ... import AiortcAdapter`` still works through
# the module-level __getattr__ below.
_LAZY_ADAPTERS = {
    "AiortcAdapter": ".aiortc_adapter",
    "MediasoupAdapter": ".mediasoup",
    "MediasoupWSAdapter": ".mediasoup_ws",
    "JanusAdapter": ".janus",
}


def __getattr__(name: str):
    module_name = _LAZY_ADAPTERS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(module_name, __name__)
    return getattr(module, name)


__all__ = [
    "SFUAdapter",
    "SFUTransport",
    "SFUProducer",
    "SFUConsumer",
    "RoomInfo",
    "AcousticDefenseConfig",
    "AudioProcessingTrack",
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
            - acoustic_defense: AcousticDefenseConfig instance

    Returns:
        SFUAdapter instance
    """
    acoustic_defense = kwargs.get("acoustic_defense")
    if backend == "aiortc":
        from .aiortc_adapter import AiortcAdapter

        return AiortcAdapter(
            ice_servers=kwargs.get("ice_servers"),
            acoustic_defense=acoustic_defense,
        )
    elif backend == "mediasoup-ws":
        from .mediasoup_ws import MediasoupWSAdapter

        return MediasoupWSAdapter(
            ws_url=kwargs.get("ws_url", kwargs.get("api_url", "wss://localhost:4443")),
            timeout=kwargs.get("timeout", 10),
            origin=kwargs.get("origin", "https://localhost"),
            acoustic_defense=acoustic_defense,
        )
    elif backend == "mediasoup":
        from .mediasoup import MediasoupAdapter

        return MediasoupAdapter(
            api_url=kwargs.get("api_url", "http://localhost:3000"),
            timeout=kwargs.get("timeout", 10),
            acoustic_defense=acoustic_defense,
        )
    elif backend == "janus":
        from .janus import JanusAdapter

        return JanusAdapter(
            api_url=kwargs.get("api_url", "http://localhost:8088/janus"),
            timeout=kwargs.get("timeout", 10),
            acoustic_defense=acoustic_defense,
        )
    else:
        raise ValueError(
            f"Unknown SFU backend: {backend}. Supported: aiortc, mediasoup-ws, mediasoup, janus"
        )
