"""
Custom exceptions raised by the channel ratchet sub-package.
"""


class ChannelRatchetError(RuntimeError):
    """Base class for all channel ratchet failures."""


class RatchetIntervalNotFoundError(ChannelRatchetError):
    """Raised when a referenced ratchet interval does not exist."""


class RatchetIntervalClosedError(ChannelRatchetError):
    """Raised when an operation requires the interval to be open."""


class RatchetKeyWrapError(ChannelRatchetError):
    """Raised when a start key cannot be wrapped or unwrapped."""


class RatchetRotationDisabledError(ChannelRatchetError):
    """Raised when rotation is requested but disabled by configuration."""
