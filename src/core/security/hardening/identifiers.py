"""Rules for names interpolated into SQL statements.

``PRAGMA``, ``ALTER TABLE``, ``DROP TABLE/INDEX/TRIGGER`` and the table/column
halves of a dynamic ``INSERT``/``UPDATE`` cannot take placeholders, so the name
is concatenated. This module is the one place that decides which names are
allowed to be concatenated: a plain identifier, bounded in length.
"""

from __future__ import annotations

import re
from typing import Any

from .errors import HardeningViolation

#: Letters, digits and underscores; never starting with a digit; bounded so a
#: hostile name cannot smuggle syntax or blow up a statement planner. Matched
#: with ``fullmatch``: Python's ``$`` also matches just before a final ``\n``,
#: which let ``"users\n"`` pass as an identifier and put a newline into a
#: ``PRAGMA``/``ALTER``/``DROP`` statement.
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}")


def is_identifier(value: Any) -> bool:
    """Whether ``value`` is safe to interpolate as a SQL object name."""
    return isinstance(value, str) and bool(IDENTIFIER.fullmatch(value))


def require_identifier(value: Any, field: str = "identifier") -> str:
    """Return ``value`` if it is a safe SQL object name, else raise."""
    if not is_identifier(value):
        raise HardeningViolation(f"{field} is not a valid SQL identifier")
    return value
