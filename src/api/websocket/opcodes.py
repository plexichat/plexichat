"""
Gateway opcodes - Standard opcode definitions for WebSocket gateway.
"""

from enum import IntEnum

from src.core.security.hardening import HardeningViolation, bounded_int


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
    # Plexichat extensions for server status
    SERVER_STATUS = 12
    VERSION_CHECK = 13
    # Plexichat voice signaling opcodes
    VOICE_CONNECT = 20
    VOICE_DISCONNECT = 21
    VOICE_SDP_OFFER = 22
    VOICE_SDP_ANSWER = 23
    VOICE_ICE_CANDIDATE = 24
    VOICE_SPEAKING = 25
    VOICE_QUALITY = 26
    # Plexichat application interaction opcodes
    INTERACTION_CREATE = 30
    INTERACTION_RESPONSE = 31
    # Plexichat typing indicator opcodes
    TYPING_START = 40
    TYPING_STOP = 41
    # Plexichat channel ratchet opcodes
    RATCHET_UPDATE = 50
    # Plexichat artifacts real-time fabric opcodes (collaborative whiteboards,
    # live code editors, and other shared artifact canvases).
    ARTIFACT_SUBSCRIBE = 60
    ARTIFACT_UNSUBSCRIBE = 61
    ARTIFACT_OP = 62
    ARTIFACT_SYNC = 63
    # Server-to-client: an op was rejected (e.g. stale revision) and the
    # sender should re-fetch/re-base instead of retrying it.
    ARTIFACT_OP_REJECTED = 68
    # Plexichat PlexiSum real-time fabric opcodes (collaborative workbooks).
    SUM_SUBSCRIBE = 64
    SUM_UNSUBSCRIBE = 65
    SUM_OP = 66
    SUM_SYNC = 67


#: Every opcode the protocol defines.
KNOWN_OPCODES = frozenset(int(op) for op in GatewayOpcode)

#: Opcodes a client is permitted to send. Server-push-only opcodes and the
#: Artifact/PlexiSum sync/rejection frames are deliberately absent: an echo of
#: one of those is soft-rejected by the handler instead of being acted upon.
CLIENT_OPCODES = frozenset(
    {
        GatewayOpcode.HEARTBEAT,
        GatewayOpcode.IDENTIFY,
        GatewayOpcode.RESUME,
        GatewayOpcode.PRESENCE_UPDATE,
        GatewayOpcode.VOICE_STATE_UPDATE,
        GatewayOpcode.REQUEST_GUILD_MEMBERS,
        GatewayOpcode.VOICE_CONNECT,
        GatewayOpcode.VOICE_DISCONNECT,
        GatewayOpcode.VOICE_SDP_OFFER,
        GatewayOpcode.VOICE_SDP_ANSWER,
        GatewayOpcode.VOICE_ICE_CANDIDATE,
        GatewayOpcode.VOICE_SPEAKING,
        GatewayOpcode.VOICE_QUALITY,
        GatewayOpcode.TYPING_START,
        GatewayOpcode.TYPING_STOP,
        GatewayOpcode.ARTIFACT_SUBSCRIBE,
        GatewayOpcode.ARTIFACT_UNSUBSCRIBE,
        GatewayOpcode.ARTIFACT_OP,
        GatewayOpcode.SUM_SUBSCRIBE,
        GatewayOpcode.SUM_UNSUBSCRIBE,
        GatewayOpcode.SUM_OP,
        # Extension opcodes with no server-side consumer yet.
        GatewayOpcode.SERVER_STATUS,
        GatewayOpcode.VERSION_CHECK,
        GatewayOpcode.INTERACTION_CREATE,
        GatewayOpcode.INTERACTION_RESPONSE,
        GatewayOpcode.RATCHET_UPDATE,
    }
)

#: Opcodes that only ever travel server -> client.
SERVER_ONLY_OPCODES = frozenset(KNOWN_OPCODES - {int(op) for op in CLIENT_OPCODES})


def coerce_opcode(value: object) -> int:
    """Return ``value`` as a known opcode integer, or raise.

    Booleans are rejected even though ``bool`` subclasses ``int``: a JSON
    ``true`` must not silently become ``HEARTBEAT`` (opcode 1). String
    opcodes are rejected too so a numeric-looking string cannot smuggle an
    opcode past the allowlist.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise HardeningViolation("opcode must be an integer")
    code = bounded_int(
        value, "opcode", minimum=min(KNOWN_OPCODES), maximum=max(KNOWN_OPCODES)
    )
    if code not in KNOWN_OPCODES:
        raise HardeningViolation("unknown opcode")
    return code


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
    # Plexichat extensions
    VERSION_OUTDATED = 4015
    SERVER_MAINTENANCE = 4016
    SERVER_SHUTDOWN = 4017
    # An inbound frame exceeded the configured/absolute byte ceiling. Kept
    # distinct from DECODE_ERROR so clients and dashboards can tell a
    # size violation apart from malformed JSON.
    MESSAGE_TOO_BIG = 4019

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
    GatewayCloseCode.MESSAGE_TOO_BIG: "Message exceeds the maximum allowed size",
    GatewayCloseCode.NORMAL_CLOSURE: "Normal closure",
    GatewayCloseCode.GOING_AWAY: "Going away",
}


RESUMABLE_CLOSE_CODES = {
    GatewayCloseCode.UNKNOWN_ERROR,
    GatewayCloseCode.UNKNOWN_OPCODE,
    GatewayCloseCode.DECODE_ERROR,
    GatewayCloseCode.MESSAGE_TOO_BIG,
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
    try:
        code = GatewayCloseCode(close_code)
    except (TypeError, ValueError):
        return "Unknown close code"
    return CLOSE_CODE_MESSAGES.get(code, "Unknown close code")
