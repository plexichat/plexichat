"""
SFU adapters - Selective Forwarding Unit integrations.

Provides adapters for mediasoup and Janus SFU servers.
"""

from .base import SFUAdapter, SFUTransport, SFUProducer, SFUConsumer
from .mediasoup import MediasoupAdapter
from .janus import JanusAdapter

__all__ = [
    "SFUAdapter",
    "SFUTransport",
    "SFUProducer",
    "SFUConsumer",
    "MediasoupAdapter",
    "JanusAdapter",
    "create_adapter",
]


def create_adapter(backend: str, **kwargs) -> SFUAdapter:
    """
    Create an SFU adapter for the specified backend.
    
    Args:
        backend: SFU backend name ("mediasoup" or "janus")
        **kwargs: Backend-specific configuration
        
    Returns:
        SFUAdapter instance
    """
    if backend == "mediasoup":
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
        raise ValueError(f"Unknown SFU backend: {backend}")
