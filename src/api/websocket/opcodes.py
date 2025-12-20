"""
Gateway opcodes - Standard opcode definitions for WebSocket gateway.
"""

from enum import IntEnum


class GatewayOpcode(IntEnum):
    """Gateway operation codes."""

    DISPATCH = 0
    HEARTBEAT = 1
    IDENTIFY = 2
    PRESENCE_UPDATE = 3
    VOICE_STATE_UPDATE = 4
    RESUME = 6
    RECONNECT = 7
    REQUEST_GUILD_MEMBERS = 8
    INVALID_SESSION = 9
    HELLO = 10
    HEARTBEAT_ACK = 11
    # PlexiChat extensions for server status
    SERVER_STATUS = 12
    VERSION_CHECK = 13
    # PlexiChat voice signaling opcodes
    VOICE_CONNECT = 20
    VOICE_DISCONNECT = 21
    VOICE_SDP_OFFER = 22
    VOICE_SDP_ANSWER = 23
    VOICE_ICE_CANDIDATE = 24
    VOICE_SPEAKING = 25
    VOICE_QUALITY = 26
    # PlexiChat application interaction opcodes
    INTERACTION_CREATE = 30
    INTERACTION_RESPONSE = 31


class GatewayCloseCode(IntEnum):
    """Gateway close codes."""

    UNKNOWN_ERROR = 4000
    UNKNOWN_OPCODE = 4001
    DECODE_ERROR = 4002
    NOT_AUTHENTICATED = 4003
    AUTHENTICATION_FAILED = 4004
    ALREADY_AUTHENTICATED = 4005
    INVALID_SEQ = 4007
    RATE_LIMITED = 4008
    SESSION_TIMED_OUT = 4009
    INVALID_SHARD = 4010
    SHARDING_REQUIRED = 4011
    INVALID_API_VERSION = 4012
    INVALID_INTENTS = 4013
    DISALLOWED_INTENTS = 4014
    # PlexiChat extensions
    VERSION_OUTDATED = 4015
    SERVER_MAINTENANCE = 4016
    SERVER_SHUTDOWN = 4017

    NORMAL_CLOSURE = 1000
    GOING_AWAY = 1001


CLOSE_CODE_MESSAGES = {
    GatewayCloseCode.UNKNOWN_ERROR: "Unknown error",
    GatewayCloseCode.UNKNOWN_OPCODE: "Unknown opcode",
    GatewayCloseCode.DECODE_ERROR: "Decode error",
    GatewayCloseCode.NOT_AUTHENTICATED: "Not authenticated",
    GatewayCloseCode.AUTHENTICATION_FAILED: "Authentication failed",
    GatewayCloseCode.ALREADY_AUTHENTICATED: "Already authenticated",
    GatewayCloseCode.INVALID_SEQ: "Invalid sequence",
    GatewayCloseCode.RATE_LIMITED: "Rate limited",
    GatewayCloseCode.SESSION_TIMED_OUT: "Session timed out",
    GatewayCloseCode.INVALID_SHARD: "Invalid shard",
    GatewayCloseCode.SHARDING_REQUIRED: "Sharding required",
    GatewayCloseCode.INVALID_API_VERSION: "Invalid API version",
    GatewayCloseCode.INVALID_INTENTS: "Invalid intents",
    GatewayCloseCode.DISALLOWED_INTENTS: "Disallowed intents",
    GatewayCloseCode.VERSION_OUTDATED: "Client version outdated",
    GatewayCloseCode.SERVER_MAINTENANCE: "Server entering maintenance",
    GatewayCloseCode.SERVER_SHUTDOWN: "Server shutting down",
}


RESUMABLE_CLOSE_CODES = {
    GatewayCloseCode.UNKNOWN_ERROR,
    GatewayCloseCode.UNKNOWN_OPCODE,
    GatewayCloseCode.DECODE_ERROR,
    GatewayCloseCode.NOT_AUTHENTICATED,
    GatewayCloseCode.ALREADY_AUTHENTICATED,
    GatewayCloseCode.INVALID_SEQ,
    GatewayCloseCode.RATE_LIMITED,
    GatewayCloseCode.SESSION_TIMED_OUT,
}


def is_resumable(close_code: int) -> bool:
    """Check if a close code allows session resume."""
    return close_code in RESUMABLE_CLOSE_CODES


def get_close_message(close_code: int) -> str:
    """Get human-readable message for close code."""
    return CLOSE_CODE_MESSAGES.get(GatewayCloseCode(close_code), "Unknown close code")
