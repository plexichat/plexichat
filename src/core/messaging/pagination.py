"""Keyset (cursor) pagination primitives for message lists.

BEFORE: deep pages were expressed as an OFFSET into the conversation. SQLite
must walk and discard every skipped row, so page N costs O(N) work and a large
channel gets progressively slower to page through.

AFTER: a page is addressed by the last message id seen (a snowflake that is
monotonically increasing with creation time). The database seeks straight to
that id through the primary-key index with ``id < ?`` / ``id > ?``; no rows are
scanned or discarded and cost is independent of how deep the caller pages.

The cursor is an opaque, bounded, signed-by-construction token so clients
cannot inject arbitrary SQL or absurd ids into the range predicate.
"""

import base64
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

_MAX_CURSOR_LENGTH = 128


@dataclass
class MessagePage:
    """One keyset page of messages plus the token needed to fetch the next."""

    items: List[Any]
    next_cursor: Optional[str] = None
    has_more: bool = False


class MessageCursor:
    """Encode/decode an opaque ``(direction, snowflake)`` page cursor."""

    @staticmethod
    def encode(direction: str, message_id: Any) -> str:
        if direction not in ("before", "after"):
            raise ValueError("cursor direction must be 'before' or 'after'")
        raw = f"{direction}:{int(message_id)}".encode("ascii")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def decode(token: Optional[str]) -> Tuple[Optional[str], Optional[int]]:
        """Return ``(direction, message_id)`` or ``(None, None)`` for no token.

        Malformed / oversized tokens are treated as "no cursor" rather than
        raising: a client must never be able to turn a bad token into a 500.
        """
        if not token or not isinstance(token, str):
            return None, None
        if len(token) > _MAX_CURSOR_LENGTH:
            return None, None
        try:
            padded = token + "=" * (-len(token) % 4)
            raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("ascii")
            direction, _, value = raw.partition(":")
            if direction not in ("before", "after") or not value.isdigit():
                return None, None
            message_id = int(value)
            if message_id < 0 or message_id >= 1 << 64:
                return None, None
            return direction, message_id
        except (ValueError, UnicodeDecodeError, base64.binascii.Error):
            return None, None
