"""
Signaling exceptions - All WebRTC signaling error types.
"""


class SignalingError(Exception):
    """Base exception for all signaling errors."""

    pass


class SDPError(SignalingError):
    """Base exception for SDP-related errors."""

    pass


class SDPParseError(SDPError):
    """Failed to parse SDP message."""

    def __init__(self, message: str, line: int = 0, detail: str = ""):
        super().__init__(message)
        self.line = line
        self.detail = detail


class SDPValidationError(SDPError):
    """SDP message failed validation."""

    def __init__(self, message: str, field: str = "", reason: str = ""):
        super().__init__(message)
        self.field = field
        self.reason = reason


class ICEError(SignalingError):
    """Base exception for ICE-related errors."""

    pass


class ICECandidateError(ICEError):
    """Invalid or failed ICE candidate."""

    def __init__(self, message: str, candidate: str = ""):
        super().__init__(message)
        self.candidate = candidate


class TURNError(SignalingError):
    """Base exception for TURN-related errors."""

    pass


class TURNCredentialError(TURNError):
    """Failed to generate TURN credentials."""

    pass


class SFUError(SignalingError):
    """Base exception for SFU-related errors."""

    pass


class SFUConnectionError(SFUError):
    """Failed to connect to SFU server."""

    def __init__(self, message: str, backend: str = "", url: str = ""):
        super().__init__(message)
        self.backend = backend
        self.url = url


class SFUTimeoutError(SFUError):
    """SFU operation timed out."""

    def __init__(self, message: str, operation: str = "", timeout_ms: int = 0):
        super().__init__(message)
        self.operation = operation
        self.timeout_ms = timeout_ms


class ConnectionError(SignalingError):
    """Base exception for connection-related errors."""

    pass


class NotConnectedError(ConnectionError):
    """User is not connected to voice."""

    def __init__(self, message: str, user_id: int = 0, channel_id: int = 0):
        super().__init__(message)
        self.user_id = user_id
        self.channel_id = channel_id


class AlreadyConnectedError(ConnectionError):
    """User is already connected to voice."""

    def __init__(self, message: str, user_id: int = 0, channel_id: int = 0):
        super().__init__(message)
        self.user_id = user_id
        self.channel_id = channel_id


class ScreenShareError(SignalingError):
    """Screen sharing operation failed."""

    def __init__(self, message: str, user_id: int = 0, reason: str = ""):
        super().__init__(message)
        self.user_id = user_id
        self.reason = reason
