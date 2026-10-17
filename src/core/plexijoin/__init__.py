"""
PlexiJoin federation module for inter-instance communication.
"""

from .manager import (
    FEDERATION_JOIN_REQUEST_PATH,
    FEDERATION_MESSAGE_PATH,
    PlexiJoinManager,
    RemoteURLBlockedError,
    validate_remote_url,
)

__all__ = [
    "PlexiJoinManager",
    "RemoteURLBlockedError",
    "validate_remote_url",
    "FEDERATION_JOIN_REQUEST_PATH",
    "FEDERATION_MESSAGE_PATH",
]
