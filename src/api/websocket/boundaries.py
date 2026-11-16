"""Inbound boundary validation for the gateway.

Every value that arrives on a WebSocket frame (or in a Join HTTP body) is
attacker-controlled JSON. This module is the one place where those values are
checked against the defense-in-depth helpers in
:mod:`src.core.security.hardening` before they can drive parsing, fan-out or
persistence.

The rules are deliberately conservative: an oversized, ill-typed or deeply
nested value is refused rather than being "fixed up" and allowed through. A
malformed client may lose a single frame; the server never pays for unbounded
parsing or allocation on its behalf.
"""

from __future__ import annotations

import json
import re

from typing import Any

from .opcodes import GatewayOpcode
from src.core.security.hardening import (
    HardeningViolation,
    bounded_int,
    parse_id,
    reject_control_characters,
)

# --- Frame / message size bounds (commit 1) ---------------------------------

#: Absolute ceiling for a single inbound frame, independent of configuration.
#: An operator may configure a *smaller* limit; a larger one is clamped here so
#: a bad config entry cannot silently disable the guard.
MAX_FRAME_BYTES = 256 * 1024

#: Ceiling for the decompressed form of a single frame.
MAX_DECOMPRESSED_BYTES = 1024 * 1024

#: Structural bounds applied to an already-decoded frame. JSON parsing itself
#: is bounded by the byte ceilings above, but a small frame can still describe
#: a pathologically deep or wide object (which then blows up recursive helpers
#: such as ``stringify_snowflake_ids``), so the decoded shape is bounded too.
MAX_PARSED_DEPTH = 12
MAX_PARSED_KEYS = 128
MAX_PARSED_STRING_BYTES = 64 * 1024


def validate_frame_bytes(data: bytes) -> bool:
    """Whether a raw frame is small enough to parse.

    This is called on the raw ``text``/``bytes`` payload *before* ``json.loads``
    so a hostile frame is rejected without ever being decoded.
    """
    try:
        size = len(data)
    except TypeError:
        return False
    return 0 <= size <= MAX_FRAME_BYTES


def validate_parsed_structure(value: Any, _depth: int = 0) -> None:
    """Reject decoded frames whose shape would make downstream work unbounded.

    Raises :class:`HardeningViolation` when nesting depth, object width or an
    individual string value exceeds its ceiling. Containers are walked once;
    the function never mutates ``value``.
    """
    if _depth > MAX_PARSED_DEPTH:
        raise HardeningViolation("gateway frame nesting too deep")
    if isinstance(value, dict):
        if len(value) > MAX_PARSED_KEYS:
            raise HardeningViolation("gateway frame has too many keys")
        for key, item in value.items():
            if isinstance(key, str) and len(key.encode("utf-8", "ignore")) > 256:
                raise HardeningViolation("gateway frame key too long")
            validate_parsed_structure(item, _depth + 1)
    elif isinstance(value, (list, tuple)):
        if len(value) > MAX_PARSED_KEYS:
            raise HardeningViolation("gateway frame sequence too long")
        for item in value:
            validate_parsed_structure(item, _depth + 1)
    elif isinstance(value, str):
        if len(value.encode("utf-8", "ignore")) > MAX_PARSED_STRING_BYTES:
            raise HardeningViolation("gateway frame string too long")


def clamp_configured_limit(value: Any, field: str, ceiling: int, default: int) -> int:
    """Return a config-provided byte limit clamped into ``[1, ceiling]``."""
    return bounded_int(value, field, minimum=1, maximum=ceiling, default=default)


def decode_gateway_frame(raw: bytes) -> dict:
    """Decode one raw text frame into a bounded JSON object.

    Combines the pre-parse byte ceiling, UTF-8 decoding, JSON parsing, the
    object-shape requirement and the structural bound into one call so a new
    receive path cannot accidentally skip one of them. Raises
    :class:`HardeningViolation` on any failure.
    """
    if not validate_frame_bytes(raw):
        raise HardeningViolation("gateway frame exceeds the byte ceiling")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HardeningViolation("gateway frame is not valid UTF-8") from exc
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise HardeningViolation("gateway frame is not valid JSON") from exc
    if not isinstance(value, dict):
        raise HardeningViolation("gateway frame is not a JSON object")
    validate_parsed_structure(value)
    return value


# --- Subscription caps (commit 3) ------------------------------------------

#: A single connection may hold at most this many artifact/workbook
#: subscriptions. Without a cap one socket can grow the registry without
#: limit and force every relay to fan out to thousands of phantom targets.
MAX_SUBSCRIPTIONS_PER_CONNECTION = 128


def subscription_allowed(existing_count: int, artifact_id: int, subscribed_ids) -> bool:
    """Whether a connection may add ``artifact_id`` to its subscription set.

    Re-subscribing to an id it already holds is always allowed (idempotent).
    """
    if artifact_id in subscribed_ids:
        return True
    return int(existing_count) < MAX_SUBSCRIPTIONS_PER_CONNECTION


# --- Queued operation bounds (commit 4) -----------------------------------
#: Upper bound for a single relayed/persisted operation envelope.
MAX_OP_PAYLOAD_BYTES = 256 * 1024

#: Upper bound for a replay/batch of operations returned in one frame.
MAX_OP_BATCH = 500

#: Upper bound for one event stored in a session's replay buffer.
MAX_REPLAY_EVENT_BYTES = 256 * 1024


def payload_size(value: object) -> int:
    """Serialized byte size of a payload; oversized/unserializable -> too big.

    Uses ``default=str`` so an unusual but JSON-friendly object is still
    measurable; anything that cannot be serialized is treated as exceeding
    the cap rather than allowed through.
    """
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))
    except (TypeError, ValueError, RecursionError):
        return MAX_OP_PAYLOAD_BYTES + 1


def bounded_batch(value: object, default: int = MAX_OP_BATCH) -> int:
    """Coerce a replay/batch limit into ``[1, MAX_OP_BATCH]``."""
    return bounded_int(
        value, "batch size", minimum=1, maximum=MAX_OP_BATCH, default=default
    )


# --- Snowflake ids (commit 5) ---------------------------------------------
def parse_snowflake(value: object, field: str = "id") -> int:
    """Parse an id in ``[1, 2**63-1]`` from an untrusted gateway payload.

    Bare ``int()`` accepted negatives and oversized values that then flowed
    into repository lookups, replay offsets and delta fan-out. Delegates to
    the shared range guard so the rule stays identical across boundaries.
    """
    return parse_id(value, field)


# --- Presence / status payloads (commit 6) -------------------------------
MAX_STATUS_LEN = 32
MAX_CUSTOM_STATUS_LEN = 128
MAX_EMOJI_LEN = 64
MAX_ACTIVITY_NAME_LEN = 128
MAX_ACTIVITY_TEXT_LEN = 256
MAX_ACTIVITIES = 5


def _bounded_text(value: object, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise HardeningViolation(f"{field} must be a string")
    text = reject_control_characters(value, field)
    if len(text) > limit:
        raise HardeningViolation(f"{field} exceeds {limit} characters")
    return text


def validate_presence_payload(data: object) -> dict:
    """Validate and normalize a PRESENCE_UPDATE payload.

    Status/custom-status/emoji/activity text is attacker-controlled and was
    copied into persisted rows and broadcast frames without limits. Control
    characters are refused and every field is length-bounded; the returned
    dict carries only validated values for the handler to use.
    """
    if not isinstance(data, dict):
        raise HardeningViolation("presence payload must be an object")

    status = _bounded_text(data.get("status", "online"), "status", MAX_STATUS_LEN)

    custom_status = None
    if data.get("custom_status") is not None:
        custom_status = _bounded_text(
            data["custom_status"], "custom_status", MAX_CUSTOM_STATUS_LEN
        )
    custom_emoji = None
    if data.get("custom_emoji") is not None:
        custom_emoji = _bounded_text(
            data["custom_emoji"], "custom_emoji", MAX_EMOJI_LEN
        )

    activities = data.get("activities", [])
    if not isinstance(activities, list):
        raise HardeningViolation("activities must be a list")
    if len(activities) > MAX_ACTIVITIES:
        raise HardeningViolation("too many activities")
    clean_activities = []
    for activity in activities:
        if not isinstance(activity, dict):
            raise HardeningViolation("activity must be an object")
        clean = {"type": activity.get("type", "custom")}
        for key, limit in (
            ("name", MAX_ACTIVITY_NAME_LEN),
            ("details", MAX_ACTIVITY_TEXT_LEN),
            ("state", MAX_ACTIVITY_TEXT_LEN),
        ):
            if activity.get(key) is not None:
                clean[key] = _bounded_text(activity[key], key, limit)
        clean_activities.append(clean)

    return {
        "status": status,
        "custom_status": custom_status,
        "custom_emoji": custom_emoji,
        "activities": clean_activities,
    }


# --- Mutating opcode authentication (commit 7) ----------------------------
#: Opcodes that change server-side state or cause a fan-out. IDENTIFY/RESUME
#: (which establish authentication) and HEARTBEAT (liveness only) are
#: deliberately absent. Resource-level authorization remains delegated to the
#: owning manager inside each handler; this set guarantees the *identity*
#: gate is applied uniformly before any of them can run.
MUTATING_OPCODES = frozenset(
    int(op)
    for op in (
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
        GatewayOpcode.INTERACTION_CREATE,
        GatewayOpcode.INTERACTION_RESPONSE,
        GatewayOpcode.RATCHET_UPDATE,
    )
)


def requires_authentication(opcode: int) -> bool:
    """Whether ``opcode`` mutates state and therefore needs an identified user."""
    return int(opcode) in MUTATING_OPCODES


# --- Reconnect / ticket bounds (commit 8) ---------------------------------
#: Tickets are machine tokens, never free-form text: no whitespace, no C0
#: controls, and a bounded length. The character class covers the URL-safe
#: alphabet plus the separators used by existing token formats.
TICKET_PATTERN = re.compile(r"^[A-Za-z0-9._~+/-:=@]+$")
MIN_TICKET_LEN = 1
MAX_TICKET_LEN = 4096
MAX_SESSION_ID_LEN = 128
SESSION_ID_PATTERN = re.compile(r"^[0-9a-fA-F]+$")
MAX_RESUME_SEQUENCE = 1_000_000
MIN_BACKOFF_MS = 0
MAX_BACKOFF_MS = 300_000
MAX_PROPERTY_LEN = 256
MAX_PROPERTIES = 16


def validate_ticket(value: object, field: str = "token") -> str:
    """Return ``value`` as a well-formed, bounded auth ticket or raise."""
    if not isinstance(value, str):
        raise HardeningViolation(f"{field} must be a string")
    text = reject_control_characters(value, field)
    if not (MIN_TICKET_LEN <= len(text) <= MAX_TICKET_LEN):
        raise HardeningViolation(f"{field} length is out of range")
    if not TICKET_PATTERN.match(text):
        raise HardeningViolation(f"{field} has an invalid format")
    return text


def validate_session_id(value: object) -> str:
    """Validate a resume session id (hex token of bounded length)."""
    if not isinstance(value, str) or not (1 <= len(value) <= MAX_SESSION_ID_LEN):
        raise HardeningViolation("session_id length is out of range")
    if not SESSION_ID_PATTERN.match(value):
        raise HardeningViolation("session_id has an invalid format")
    return value


def bounded_resume_sequence(value: object) -> int:
    """Bound a client-supplied resume sequence into a sane replay window."""
    return bounded_int(
        value,
        "seq",
        minimum=0,
        maximum=MAX_RESUME_SEQUENCE,
        default=0,
    )


def bounded_backoff(value: object, field: str = "backoff") -> int:
    """Bound a reconnect/backoff duration in milliseconds."""
    return bounded_int(
        value, field, minimum=MIN_BACKOFF_MS, maximum=MAX_BACKOFF_MS, default=0
    )


def validate_properties(value: object) -> dict:
    """Bound the IDENTIFY ``properties`` map without changing valid values."""
    if not isinstance(value, dict):
        raise HardeningViolation("properties must be an object")
    if len(value) > MAX_PROPERTIES:
        raise HardeningViolation("too many client properties")
    for key, item in value.items():
        if not isinstance(key, str) or len(key) > MAX_PROPERTY_LEN:
            raise HardeningViolation("property key is too long")
        if isinstance(item, str) and len(item) > MAX_PROPERTY_LEN:
            raise HardeningViolation("property value is too long")
    return value
