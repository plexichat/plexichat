"""
Base classes for admin authentication.
"""

from typing import Any


class AdminAuthBase:
    """Base class providing the database session to all mixins."""

    __slots__ = ("_db",)

    def __init__(self, db: Any) -> None:
        self._db = db
