"""
Base class for admin users route handlers.

Provides shared helpers used by all mixins via multiple inheritance.
"""

from typing import Protocol, Any
from fastapi import HTTPException
import src.api as api
from src.core.security.hardening import HardeningViolation, parse_id


class AdminUsersRouterProtocol(Protocol):
    def _get_db(self) -> Any: ...

    def _get_auth(self) -> Any: ...

    def _parse_user_id(self, user_id: str) -> int: ...


class AdminUsersRouterBase(AdminUsersRouterProtocol):
    def _parse_user_id(self, user_id: str) -> int:
        """Parse a user-id path segment with the shared ASCII snowflake gate.

        A bare ``int(user_id)`` accepted Unicode decimal homoglyphs
        (``"٤٢"``/``"４２"`` -> 42), PEP-515 separators (``"1_2"`` -> 12),
        signs/whitespace and arbitrary-precision or negative values. The
        handler then used that normalized id for admin lookups and cache keys
        even though every other route boundary parses snowflakes through the
        canonical ``parse_id`` gate. Route the whole admin-users family
        through the same gate so a token the client/next hop reads as text
        cannot be silently reinterpreted as a different id here.

        ``HardeningViolation`` is a ``ValueError``, so the surrounding
        handlers keep their existing 400 contract; we still map it explicitly
        here to keep this helper's own contract unchanged.
        """
        try:
            return parse_id(user_id, "user_id")
        except (HardeningViolation, TypeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid user ID"}},
            )

    def _get_db(self):
        db = api.get_db()
        if db is None:
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Database not available"}},
            )
        return db

    def _get_auth(self):
        auth = api.get_auth()
        if auth is None:
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Auth module not available"}},
            )
        return auth
