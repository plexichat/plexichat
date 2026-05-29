from typing import Any, Dict, Optional, List

from src.core.base import SnowflakeID
from ..exceptions import (
    InvalidServerNameError,
    InvalidChannelNameError,
    InvalidRoleNameError,
)
from .protocol import ServerProtocol


class CacheOpsMixin(ServerProtocol):
    """Mixin for cache and validation operations."""

    _cache_ttl: int = 60
    _member_cache_prefix: str = "srv_member:"
    _permission_cache_prefix: str = "srv_permission:"
    _channel_cache_prefix: str = "srv_channel:"
    _server_owner_cache_prefix: str = "srv_owner:"
    _member_roles_cache_prefix: str = "srv_member_roles:"

    def _cache_get(self, prefix: str, key: Any, default: Any = None) -> Any:
        cache_key = f"{prefix}{key}"
        from src.core.database import cache_get

        return cache_get(cache_key) or default

    def _cache_set(self, prefix: str, key: Any, value: Any) -> None:
        cache_key = f"{prefix}{key}"
        from src.core.database import cache_set

        cache_set(cache_key, value, ttl=self._cache_ttl)

    def _cache_invalidate(self, prefix: str, key: Optional[Any] = None) -> None:
        from src.core.database import invalidate_pattern, cache_delete

        if key is None:
            invalidate_pattern(f"{prefix}*")
        else:
            cache_key = f"{prefix}{key}"
            cache_delete(cache_key)

    def _validate_server_name(self, name: str) -> str:
        if not name or not name.strip():
            raise InvalidServerNameError("Server name cannot be empty")

        name = name.strip()
        min_len = self._config.get("server_name_min_length", 2)
        max_len = self._config.get("server_name_max_length", 100)

        if len(name) < min_len:
            raise InvalidServerNameError(
                f"Server name must be at least {min_len} characters", name
            )
        if len(name) > max_len:
            raise InvalidServerNameError(
                f"Server name cannot exceed {max_len} characters", name
            )
        return name

    def _validate_channel_name(self, name: str) -> str:
        if not name or not name.strip():
            raise InvalidChannelNameError("Channel name cannot be empty")

        name = name.strip()

        if not name.isascii():
            raise InvalidChannelNameError("Channel name must be ASCII", name)

        if not any(ch.isalnum() for ch in name):
            raise InvalidChannelNameError(
                "Channel name must contain letters or digits", name
            )

        name = name.replace(" ", "-")

        min_len = self._config.get("channel_name_min_length", 1)
        max_len = self._config.get("channel_name_max_length", 100)

        if len(name) < min_len:
            raise InvalidChannelNameError(
                f"Channel name must be at least {min_len} characters", name
            )
        if len(name) > max_len:
            raise InvalidChannelNameError(
                f"Channel name cannot exceed {max_len} characters", name
            )
        return name

    def _validate_role_name(self, name: str) -> str:
        if not name or not name.strip():
            raise InvalidRoleNameError("Role name cannot be empty")

        name = name.strip()
        min_len = self._config.get("role_name_min_length", 1)
        max_len = self._config.get("role_name_max_length", 100)

        if len(name) < min_len:
            raise InvalidRoleNameError(
                f"Role name must be at least {min_len} characters", name
            )
        if len(name) > max_len:
            raise InvalidRoleNameError(
                f"Role name cannot exceed {max_len} characters", name
            )
        return name

    def _get_member_role_rows(
        self, server_id: SnowflakeID, user_id: SnowflakeID
    ) -> List[Dict[str, Any]]:
        member = self.get_member(server_id, user_id)
        if not member:
            return []

        rows = self._db.fetch_all(
            """SELECT r.* FROM srv_roles r
               INNER JOIN srv_member_roles mr ON r.id = mr.role_id
               WHERE mr.member_id = ? AND r.deleted = 0""",
            (member.id,),
        )

        return [dict(row) for row in rows]
