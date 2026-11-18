"""Field and identifier bounds for PlexiJoin federation records.

Remote instances supply instance ids, notes, participant rosters and room
payloads. None of these had a size limit: a single join request could stuff a
megabyte into ``note``, and a forged roster could contain thousands of
participants or oversized identifiers. Every field is bounded here with the
shared hardening primitives before it reaches the database or an outbound
envelope.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, cast

from src.core.security.hardening import (
    HardeningViolation,
    bounded_int,
    parse_id,
    reject_control_characters,
)

MAX_INSTANCE_ID_LENGTH = 128
MAX_URL_LENGTH = 2048
MAX_NOTE_LENGTH = 500
MAX_ACTOR_LENGTH = 128
MAX_ROOM_ID_LENGTH = 128
MAX_PARTICIPANT_ID_LENGTH = 128
MAX_PARTICIPANT_FIELD_LENGTH = 256
MAX_PARTICIPANTS_PER_ROOM = 100
MAX_PAGE_SIZE = 100
MAX_PAGE_NUMBER = 1_000_000
MAX_MESSAGE_COUNT = 1_000_000

_DIRECTIONS = frozenset({"inbound", "outbound"})


# Federation tokens are urlsafe base64 (letters, digits, ``-`` and ``_``).
# A restrictive character class here stops control characters, CRLF and
# delimiter injection before a token is used as a signature or header value.
# Use ``fullmatch`` (no anchors) rather than ``match`` with a ``$`` anchor: in
# Python ``$`` also matches immediately before a trailing newline, so a value
# such as ``"token\n"`` used to pass validation and flow into HMAC signature
# material, the replay-nonce key and HTTP headers — even though every other
# field validator here rejects control characters.
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]+")
MAX_TOKEN_LENGTH = 256
MAX_JOIN_CODE_LENGTH = 64
MIN_JOIN_CODE_LENGTH = 8
MAX_SHARED_KEY_LENGTH = 256
MAX_TIMESTAMP_SKEW = 300


def validate_token(
    value: Any, field: str, *, max_length: int = MAX_TOKEN_LENGTH
) -> str:
    """Return a urlsafe token if it has a well-formed, bounded shape."""
    if not isinstance(value, str) or not value:
        raise HardeningViolation(f"{field} must be a non-empty token")
    if len(value) > max_length:
        raise HardeningViolation(f"{field} exceeds {max_length} characters")
    if not _TOKEN_RE.fullmatch(value):
        raise HardeningViolation(f"{field} has an invalid format")
    # Defence in depth: keep token validation consistent with ``bound_text``,
    # which rejects C0/DEL controls. fullmatch already excludes them, but the
    # explicit check documents the contract and survives future regex edits.
    reject_control_characters(value, field)
    return value


def validate_nonce(value: Any, field: str = "nonce") -> str:
    """Validate a single-use federation nonce."""
    return validate_token(value, field, max_length=MAX_TOKEN_LENGTH)


def validate_shared_key(value: Any, *, require_strong: bool = False) -> str:
    """Validate the shape of a federation shared key.

    ``require_strong`` is used for locally generated keys (which must be
    full-strength); imported/legacy keys are only shape-checked so existing
    deployments are not broken by a format tightening.
    """
    key = validate_token(value, "shared_key", max_length=MAX_SHARED_KEY_LENGTH)
    if require_strong and len(key) < 32:
        raise HardeningViolation("shared_key is too short")
    return key


def validate_join_code(value: Any, field: str = "join_code") -> str:
    """Validate a human-readable join code (8-64 urlsafe characters)."""
    if not isinstance(value, str):
        raise HardeningViolation(f"{field} must be a string")
    if not (MIN_JOIN_CODE_LENGTH <= len(value) <= MAX_JOIN_CODE_LENGTH):
        raise HardeningViolation(f"{field} has an invalid length")
    if not _TOKEN_RE.fullmatch(value):
        raise HardeningViolation(f"{field} has an invalid format")
    reject_control_characters(value, field)
    return value


def validate_timestamp(value: Any, field: str = "timestamp") -> int:
    """Parse a unix-seconds federation timestamp with a sane ceiling."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise HardeningViolation(f"{field} must be an integer")
    if isinstance(value, str):
        # ``str.isdigit()`` is True for non-ASCII digits (superscripts, digits
        # from other scripts) that ``int()`` either cannot parse at all —
        # leaking a bare ValueError instead of a HardeningViolation — or parses
        # inconsistently. Federation timestamps are ASCII decimal strings.
        if not value.isascii() or not value.isdigit():
            raise HardeningViolation(f"{field} must be an integer")
    try:
        seconds = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        # Very long digit strings trip CPython's int() digit limit; report it
        # through the same HardeningViolation every caller already handles.
        raise HardeningViolation(f"{field} must be an integer") from exc
    if seconds < 0 or seconds > 4_102_444_800:
        raise HardeningViolation(f"{field} is outside the allowed range")
    return seconds


def validate_join_envelope(envelope: Any) -> Dict[str, Any]:
    """Validate the format of a signed join-request envelope."""
    if not isinstance(envelope, dict):
        raise HardeningViolation("join envelope must be an object")
    return {
        "instance_id": bound_text(
            envelope.get("instance_id"), "instance_id", 128, required=True
        ),
        "join_code": validate_join_code(envelope.get("join_code")),
        "nonce": validate_nonce(envelope.get("nonce")),
        "timestamp": validate_timestamp(envelope.get("timestamp")),
    }


def bound_text(
    value: Any,
    field: str,
    max_length: int,
    *,
    required: bool = False,
) -> Optional[str]:
    """Return ``value`` as a bounded, control-character-free string."""
    if value is None:
        if required:
            raise HardeningViolation(f"{field} is required")
        return None
    if not isinstance(value, str):
        raise HardeningViolation(f"{field} must be a string")
    if required and not value.strip():
        raise HardeningViolation(f"{field} is required")
    if len(value) > max_length:
        raise HardeningViolation(f"{field} exceeds {max_length} characters")
    reject_control_characters(value, field)
    return value


def bound_id(value: Any, field: str = "id") -> int:
    """Parse a federation record id as a real snowflake."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise HardeningViolation(f"{field} must be an integer id")
    return parse_id(value, field)


def bound_pagination(page: Any, per_page: Any) -> tuple[int, int]:
    """Bound the ``page``/``per_page`` window used by list endpoints."""
    return (
        bounded_int(page, "page", minimum=1, maximum=MAX_PAGE_NUMBER),
        bounded_int(per_page, "per_page", minimum=1, maximum=MAX_PAGE_SIZE),
    )


def bound_direction(value: Any) -> str:
    """Only the two traffic directions we record are accepted."""
    if value not in _DIRECTIONS:
        raise HardeningViolation("direction is not a recognised traffic direction")
    return str(value)


def bound_message_count(value: Any) -> int:
    """Bound the counter increment applied to traffic totals."""
    return bounded_int(value, "message_count", minimum=0, maximum=MAX_MESSAGE_COUNT)


def validate_room_payload(payload: Any) -> Dict[str, Any]:
    """Validate and bound a room/participant federation payload.

    The roster is de-duplicated by participant id and capped, so an inbound
    envelope cannot force an unbounded per-participant loop on the receiver.
    """
    if not isinstance(payload, dict):
        raise HardeningViolation("room payload must be an object")
    room_id = bound_text(
        payload.get("room_id"), "room_id", MAX_ROOM_ID_LENGTH, required=True
    )
    participants = payload.get("participants", [])
    if participants is None:
        participants = []
    if not isinstance(participants, (list, tuple)):
        raise HardeningViolation("participants must be a list")
    if len(participants) > MAX_PARTICIPANTS_PER_ROOM:
        raise HardeningViolation("participants exceeds the room roster cap")

    cleaned = []
    seen: set[str] = set()
    for index, participant in enumerate(participants):
        if not isinstance(participant, dict):
            raise HardeningViolation(f"participants[{index}] must be an object")
        pid = bound_text(
            participant.get("id"),
            f"participants[{index}].id",
            MAX_PARTICIPANT_ID_LENGTH,
            required=True,
        )
        if pid in seen:
            continue
        seen.add(cast(str, pid))
        cleaned.append(
            {
                "id": pid,
                "name": bound_text(
                    participant.get("name"),
                    f"participants[{index}].name",
                    MAX_PARTICIPANT_FIELD_LENGTH,
                ),
                "role": bound_text(
                    participant.get("role"),
                    f"participants[{index}].role",
                    MAX_PARTICIPANT_FIELD_LENGTH,
                ),
            }
        )
    result = dict(payload)
    result["room_id"] = room_id
    result["participants"] = cleaned
    return result


__all__ = [
    "MAX_INSTANCE_ID_LENGTH",
    "MAX_MESSAGE_COUNT",
    "MAX_NOTE_LENGTH",
    "MAX_JOIN_CODE_LENGTH",
    "MAX_PAGE_SIZE",
    "MAX_TOKEN_LENGTH",
    "MAX_PARTICIPANTS_PER_ROOM",
    "MAX_ROOM_ID_LENGTH",
    "MAX_URL_LENGTH",
    "bound_direction",
    "bound_id",
    "bound_message_count",
    "bound_pagination",
    "bound_text",
    "validate_join_code",
    "validate_join_envelope",
    "validate_nonce",
    "validate_shared_key",
    "validate_timestamp",
    "validate_token",
    "validate_room_payload",
]
