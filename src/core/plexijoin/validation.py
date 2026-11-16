"""Field and identifier bounds for PlexiJoin federation records.

Remote instances supply instance ids, notes, participant rosters and room
payloads. None of these had a size limit: a single join request could stuff a
megabyte into ``note``, and a forged roster could contain thousands of
participants or oversized identifiers. Every field is bounded here with the
shared hardening primitives before it reaches the database or an outbound
envelope.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

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
        seen.add(pid)
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
    "MAX_PAGE_SIZE",
    "MAX_PARTICIPANTS_PER_ROOM",
    "MAX_ROOM_ID_LENGTH",
    "MAX_URL_LENGTH",
    "bound_direction",
    "bound_id",
    "bound_message_count",
    "bound_pagination",
    "bound_text",
    "validate_room_payload",
]
