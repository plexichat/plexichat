"""Boundary guards for reaction emoji and bulk message-id arrays."""

from __future__ import annotations

import urllib.parse
from typing import Iterable, List

from src.core.security.hardening import HardeningViolation, parse_id

#: A single batch query must not turn into an unbounded placeholder list.
MAX_BULK_MESSAGE_IDS = 100

#: Longest reaction emoji token accepted at an HTTP boundary (custom emoji
#: markup is shorter than this in practice; the manager keeps its own rules).
MAX_REACTION_EMOJI_LENGTH = 32

# Invisible/bidirectional controls can make two visually identical reaction
# tokens collide in the UI or reorder surrounding text. They are never part of
# a legitimate emoji token. ZWJ (U+200D) is deliberately NOT in this set: it is
# required for family/profession emoji sequences.
INVISIBLE_EMOJI_CODEPOINTS = frozenset(
    {
        0x200B,
        0x200C,
        0x200E,
        0x200F,
        0x202A,
        0x202B,
        0x202C,
        0x202D,
        0x202E,
        0x2066,
        0x2067,
        0x2068,
        0x2069,
        0xFEFF,
    }
)


def validate_route_emoji(emoji: object) -> str:
    """Decode and validate an emoji path segment from an HTTP route.

    Rejects empty/oversized tokens, C0 controls, invisible bidi/ZWSP controls
    and unpaired surrogates before the value reaches the reactions table or a
    WebSocket event payload.

    Raises:
        ValueError: If the token is not a safe reaction emoji.
    """
    decoded = urllib.parse.unquote(str(emoji or ""))
    if not decoded or not decoded.strip() or len(decoded) > MAX_REACTION_EMOJI_LENGTH:
        raise ValueError("Invalid emoji")
    for ch in decoded:
        code = ord(ch)
        if code < 0x20 or code == 0x7F:
            raise ValueError("Invalid emoji")
        if code in INVISIBLE_EMOJI_CODEPOINTS:
            raise ValueError("Invalid emoji")
        if 0xD800 <= code <= 0xDFFF:
            raise ValueError("Invalid emoji")
    return decoded


def blocked_users_not_in(column: str, viewer_id: object) -> tuple[str, List[object]]:
    """Predicate excluding a viewer's blocked users with only two binds.

    A viewer's blocked set is bidirectional and has no configured cap. Binding
    one ``?`` per id (splitting them across several ``NOT IN`` conjuncts does
    not help -- SQLite caps the TOTAL number of binds per statement) crossed
    the 999/32766 variable limit and made every reaction read for that viewer
    fail with "too many SQL variables". Resolve the same bidirectional set in
    the database instead, so the number of binds stays constant no matter how
    many blocks exist. Only ``column`` is interpolated; it is always a
    hard-coded identifier supplied by the caller, never user input.
    """
    if not viewer_id:
        return "", []
    sql = (
        f" AND {column} NOT IN (SELECT blocked_id FROM rel_blocked WHERE blocker_id = ?)"
        f" AND {column} NOT IN (SELECT blocker_id FROM rel_blocked WHERE blocked_id = ?)"
    )
    return sql, [viewer_id, viewer_id]


def validate_bulk_message_ids(ids: Iterable[object]) -> List[int]:
    """Bound, parse and de-duplicate a list of message ids for batch queries.

    Raises:
        ValueError: If the list is too long or any id is not a snowflake.
    """
    if isinstance(ids, (str, bytes)) or not isinstance(ids, Iterable):
        raise ValueError("message_ids must be a sequence")
    raw = list(ids)
    if len(raw) > MAX_BULK_MESSAGE_IDS:
        raise ValueError(f"message_ids exceeds maximum of {MAX_BULK_MESSAGE_IDS} ids")
    parsed: List[int] = []
    # This helper runs on EVERY reaction-batch hydration (cold and warm cache
    # hits alike), for up to 100 ids.  Membership against the growing ``parsed``
    # list made de-duplication an O(n^2) scan -- 4,950 equality comparisons for
    # a full page, dominating the helper's cost.  Track accepted ids in a set
    # so each id is checked once; ``parsed`` still preserves first-occurrence
    # order for the caller's IN clause.
    seen = set()
    for value in raw:
        # int(True) == 1 and int(1.5) == 1, which would silently reinterpret
        # a bogus id as a real snowflake; reject those types before parsing.
        if isinstance(value, bool) or (
            isinstance(value, float) and not value.is_integer()
        ):
            raise ValueError("message_id must be an integer")
        try:
            message_id = parse_id(value, "message_id")
        except HardeningViolation as exc:
            raise ValueError(str(exc)) from exc
        if message_id not in seen:
            seen.add(message_id)
            parsed.append(message_id)
    return parsed
