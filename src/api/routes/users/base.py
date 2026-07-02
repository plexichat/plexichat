"""
Base class for user route handlers.

Provides shared helpers (cache invalidation, module access) used by all
mixins via multiple inheritance.
"""

from src.core.database import invalidate_pattern


class UsersRouterBase:
    def _invalidate_user_cache(self, user_id: int) -> None:
        invalidate_pattern(f"user_data:*{user_id}*")
        invalidate_pattern(f"user:*{user_id}*")
