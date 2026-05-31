"""
Base class for admin users route handlers.

Provides shared helpers used by all mixins via multiple inheritance.
"""

from typing import Protocol, Any
from fastapi import HTTPException
import src.api as api


class AdminUsersRouterProtocol(Protocol):
    def _get_db(self) -> Any: ...

    def _get_auth(self) -> Any: ...

    def _parse_user_id(self, user_id: str) -> int: ...


class AdminUsersRouterBase(AdminUsersRouterProtocol):
    def _parse_user_id(self, user_id: str) -> int:
        try:
            return int(user_id)
        except ValueError:
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
