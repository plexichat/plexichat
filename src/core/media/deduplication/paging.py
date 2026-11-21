"""Shared page-bound clamping for deduplication listing helpers.

Every listing method in this package binds ``limit`` straight into SQL
``LIMIT ? OFFSET ?``. SQLite reads ``LIMIT -1`` as *unlimited* and a very large
positive value as "up to N", so neither end may reach SQL unchecked. Invalid,
``None``, overflow and non-integer input fails closed to an empty page (0)
instead of raising or becoming an unbounded read.
"""

from typing import Any

from src.core.security.hardening import HardeningViolation, bounded_int

# Typical admin page size for these moderation listings (mirrors
# ``admin.moderation.MAX_PAGE_SIZE``). One caller may never materialize more
# than this many rows, however large the requested limit is.
MAX_LIST_LIMIT = 200

_SNOWFLAKE_MAX = 2**63 - 1


def clamp_page_limit(value: Any, maximum: int = MAX_LIST_LIMIT) -> int:
    """Return an integer page size in ``[0, maximum]``, failing closed to 0.

    Plain integers are clamped with ``max(0, min(value, maximum))``. Anything
    ``bounded_int`` rejects (``None``, booleans, fractions, non-finite floats,
    homoglyph/underscore strings, values beyond snowflake range) becomes 0 --
    an empty page, never an unbounded ``LIMIT``.
    """
    try:
        number = bounded_int(
            value, "limit", minimum=0, maximum=_SNOWFLAKE_MAX, default=0
        )
    except HardeningViolation:
        return 0
    return min(number, maximum)
