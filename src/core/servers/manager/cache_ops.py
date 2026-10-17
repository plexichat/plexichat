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

        value = cache_get(cache_key)
        return value if value is not None else default

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

    def _invalidate_permission_caches(
        self, server_id: SnowflakeID, user_id: SnowflakeID
    ) -> None:
        """Drop every permission result cached for *user_id* in *server_id*.

        Permission decisions are cached in three independent tiers, so all
        three must be cleared or a role/override/ownership change keeps serving
        stale RBAC results for up to the cache TTL:

        * ``perms:{user}:{server}:{channel}`` — the bare Valkey tier written by
          ``RoleHandler.get_permissions``.
        * ``srv_permission:perms:{user}:{server}:{channel}`` — the prefixed
          (memory + Valkey) tier written through ``_cache_set``.
        * ``has_perm:*`` — the ``@cached`` tier behind ``has_permission``.
        """
        from src.core.database import invalidate_pattern

        server_id = int(server_id)
        user_id = int(user_id)
        invalidate_pattern(f"perms:{user_id}:{server_id}:*")
        invalidate_pattern(f"srv_permission:perms:{user_id}:{server_id}:*")
        invalidate_pattern("has_perm:*")

    def _invalidate_server_permission_caches(self, server_id: SnowflakeID) -> None:
        """Drop every permission result cached for *server_id*.

        Role permission changes and role deletion affect every member who held
        the role, so the caches cannot be keyed to a single user. Clears both
        the bare ``perms:`` tier and the prefixed ``srv_permission:`` tier for
        the whole server, plus the ``has_perm`` decorator tier.
        """
        from src.core.database import invalidate_pattern

        server_id = int(server_id)
        invalidate_pattern(f"perms:*:{server_id}:*")
        invalidate_pattern(f"srv_permission:perms:*:{server_id}:*")
        invalidate_pattern("has_perm:*")

    def _invalidate_owner_cache(self, server_id: SnowflakeID) -> None:
        """Drop cached ownership so an ownership transfer takes effect now.

        Ownership is read from two key names (``srv_owner:`` via the prefixed
        tier and ``server_owner:`` via a bare Valkey key), so both are cleared.
        Without this, the previous owner keeps ``is_owner=True`` in
        ``get_permissions`` and therefore retains full administrator access for
        up to the owner-cache TTL after the transfer.
        """
        from src.core.database import cache_delete

        server_id = int(server_id)
        cache_delete(f"srv_owner:{server_id}")
        cache_delete(f"server_owner:{server_id}")

    def _invalidate_member_caches(
        self, server_id: SnowflakeID, user_id: SnowflakeID
    ) -> None:
        """Drop every cache entry that depends on *user_id*'s membership.

        ``is_member`` stores under two key names (``is_member:...`` and the
        prefixed ``srv_member:is_member:...`` tier), and ``get_member`` /
        ``get_servers`` keep their own caches. Membership changes (join, kick,
        ban, leave) must clear all of them so a removed user is not still
        treated as a member — or a new member denied — for the cache TTL.
        """
        from src.core.database import cache_delete, invalidate_pattern

        server_id = int(server_id)
        user_id = int(user_id)
        cache_delete(f"is_member:{server_id}:{user_id}")
        cache_delete(f"srv_member:is_member:{server_id}:{user_id}")
        invalidate_pattern(f"member_data:*{user_id}*")
        cache_delete(f"user_servers:{user_id}")
        self._invalidate_permission_caches(server_id, user_id)

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
