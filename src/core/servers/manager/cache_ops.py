from typing import Any, Dict, Optional, List

from src.core.base import SnowflakeID
from src.core.security.hardening import HardeningViolation
from src.core.security.hardening.text import reject_control_characters
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
    _roster_cache_prefix: str = "srv_roster"

    def _invalidate_roster_caches(
        self, server_id: Optional[SnowflakeID] = None
    ) -> None:
        """Drop cached per-viewer roster pages.

        A roster page embeds each member's nickname, role ids and timeout
        state, so every membership or member-field mutation must evict it for
        all viewers immediately rather than leaving stale data for the short
        ``srv_roster`` TTL.  Pass ``server_id`` to narrow the clear to one
        server; omit it to drop the whole namespace.
        """
        from src.core.database import invalidate_pattern

        if server_id is None:
            invalidate_pattern(f"{self._roster_cache_prefix}:*")
        else:
            invalidate_pattern(f"{self._roster_cache_prefix}:*:{int(server_id)}:*")

    def _permission_cache_key(self, prefix: str, key: Any) -> Any:
        """Map a permission key to the canonical form readers look up.

        ``RoleHandler.get_permissions_batch`` warms the permission cache with a
        ``(user_id, server_id, channel_id)`` tuple, while the single-channel
        ``get_permissions`` reader looks up the string key
        ``perms:{user}:{server}:{channel}``.  Composing them verbatim made the
        batch warm entries unreachable: the next channel-scoped permission
        check re-resolved owner, roles and overrides from the database, and the
        warmed entries also escaped the ``srv_permission:perms:{user}:{server}:*``
        invalidation pattern.  Normalize the tuple to the canonical key so a
        batched resolve is reusable by the per-channel checks that follow it
        and is covered by the existing invalidation.  Other cache tiers and
        key shapes are composed exactly as before.
        """
        if (
            prefix == self._permission_cache_prefix
            and isinstance(key, tuple)
            and len(key) == 3
        ):
            return f"perms:{int(key[0])}:{int(key[1])}:{int(key[2])}"
        return key

    def _cache_get(self, prefix: str, key: Any, default: Any = None) -> Any:
        cache_key = f"{prefix}{self._permission_cache_key(prefix, key)}"
        from src.core.database import cache_get

        value = cache_get(cache_key)
        return value if value is not None else default

    def _cache_set(self, prefix: str, key: Any, value: Any) -> None:
        cache_key = f"{prefix}{self._permission_cache_key(prefix, key)}"
        from src.core.database import cache_set

        cache_set(cache_key, value, ttl=self._cache_ttl)

    def _cache_invalidate(self, prefix: str, key: Optional[Any] = None) -> None:
        from src.core.database import invalidate_pattern, cache_delete

        if key is None:
            invalidate_pattern(f"{prefix}*")
        else:
            cache_key = f"{prefix}{self._permission_cache_key(prefix, key)}"
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

        from ..channel_access_cache import get_channel_access_cache

        server_id = int(server_id)
        user_id = int(user_id)
        invalidate_pattern(f"perms:{user_id}:{server_id}:*")
        invalidate_pattern(f"srv_permission:perms:{user_id}:{server_id}:*")
        invalidate_pattern("has_perm:*")
        # Bump the bounded L1 epoch so no cached channel-view decision from the
        # old permission snapshot can be replayed after this change.
        get_channel_access_cache().bump(server_id, user_id)
        from .member_role_rows_cache import invalidate_member_role_rows_cache

        invalidate_member_role_rows_cache()

    def _invalidate_server_permission_caches(self, server_id: SnowflakeID) -> None:
        """Drop every permission result cached for *server_id*.

        Role permission changes and role deletion affect every member who held
        the role, so the caches cannot be keyed to a single user. Clears both
        the bare ``perms:`` tier and the prefixed ``srv_permission:`` tier for
        the whole server, plus the ``has_perm`` decorator tier.
        """
        from src.core.database import invalidate_pattern

        from ..channel_access_cache import get_channel_access_cache

        server_id = int(server_id)
        invalidate_pattern(f"perms:*:{server_id}:*")
        invalidate_pattern(f"srv_permission:perms:*:{server_id}:*")
        invalidate_pattern("has_perm:*")
        # A role-wide change invalidates every member's channel-view decision
        # in the server; advancing the server epoch retires them all at once.
        get_channel_access_cache().bump(server_id)
        from .member_role_rows_cache import invalidate_member_role_rows_cache

        invalidate_member_role_rows_cache()

    def _invalidate_owner_cache(self, server_id: SnowflakeID) -> None:
        """Drop cached ownership so an ownership transfer takes effect now.

        Ownership is read from two key names (``srv_owner:`` via the prefixed
        tier and ``server_owner:`` via a bare Valkey key), so both are cleared.
        Without this, the previous owner keeps ``is_owner=True`` in
        ``get_permissions`` and therefore retains full administrator access for
        up to the owner-cache TTL after the transfer.
        """
        from src.core.database import cache_delete

        from ..channel_access_cache import get_channel_access_cache

        server_id = int(server_id)
        cache_delete(f"srv_owner:{server_id}")
        cache_delete(f"server_owner:{server_id}")
        # Ownership changes alter administrator/role decisions for the whole
        # server; retire every cached channel-view verdict for that scope.
        get_channel_access_cache().bump(server_id)
        from .member_role_rows_cache import invalidate_member_role_rows_cache

        invalidate_member_role_rows_cache()

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
        from .member_count_cache import invalidate_member_count_cache

        server_id = int(server_id)
        user_id = int(user_id)
        # The server-list member count is a per-server value that changes on
        # every join/kick/leave/ban/reap. Drop it here so no listing can serve
        # a stale count after this mutation (see member_count_cache).
        invalidate_member_count_cache()
        cache_delete(f"is_member:{server_id}:{user_id}")
        cache_delete(f"srv_member:is_member:{server_id}:{user_id}")
        invalidate_pattern(f"member_data:*{user_id}*")
        # The per-viewer HTTP roster cache (``server_members_api``) is NOT
        # covered by ``member_data:*``: a join/kick/ban/leave changes the roster
        # for every viewer, so evict every cached member-list page here too.
        invalidate_pattern("server_members_api:*")
        cache_delete(f"user_servers:{user_id}")
        # The bound ``get_servers`` tier (``@cached(ttl=15,
        # prefix="user_servers")``) never writes the plain ``user_servers:<uid>``
        # key deleted above: it keys entries as ``user_servers:int:<uid>``
        # (default limit) or ``user_servers:int:<uid>:...`` (explicit limit).
        # That list is derived entirely from ``srv_members``, so a kick/ban/
        # leave/reap that only reaches this core funnel left the removed
        # member reading a still-warm server list (including the server they
        # just lost) for the full 15s TTL. Drop the real decorator keys too;
        # the exact and scoped-wildcard forms cover both call shapes without
        # evicting another user's entry.
        cache_delete(f"user_servers:int:{user_id}")
        invalidate_pattern(f"user_servers:int:{user_id}:*")
        # The search layer memoizes each target user's *set of server ids*
        # under a separate key (SearchAccessMixin._get_user_servers_map) so it
        # can compute mutual-server counts without one query per hit. That
        # memo is authorization-adjacent: after a kick/ban/leave it must not
        # keep claiming the removed member still shares a server for the
        # whole cache TTL. No other membership mutation helper clears it, so
        # drop it here alongside the authoritative ``user_servers`` entry.
        cache_delete(f"user:servers:{user_id}")
        # The core per-viewer roster read (srv_roster:*) is a separate tier
        # from the HTTP one above; a join/kick/ban/leave changes it for every
        # viewer, so drop it here too.
        self._invalidate_roster_caches(server_id)
        # ``ChannelHandler.get_channels`` memoizes the permission-filtered
        # channel list with the membership gate *inside* the cached body, so a
        # warm entry keeps serving a removed member the server's channels for
        # the whole 30s TTL without re-checking membership. Role mutations drop
        # this namespace via ``_invalidate_roles_listing_cache``, but core-only
        # membership writes (kick/ban/leave/temporary reaping, including the
        # automod/admin/bot callers that never reach the HTTP revocation
        # helper) rely on this funnel alone. Evict it here so every revocation
        # closes the stale channel-list access at the core tier too.
        invalidate_pattern("server_channels:*")
        # The HTTP channel-list response is cached in its own per-viewer
        # namespace (``server_channels_api``) behind this same membership
        # gate. The router's ``_invalidate_member_access_caches`` clears it
        # for HTTP kicks, but core-only removals never reach that helper, so
        # a kicked member kept replaying a warm 200 channel list for the full
        # API TTL. Evict that namespace here as well so both list tiers fall
        # together for every revocation path.
        invalidate_pattern("server_channels_api:*")
        self._invalidate_permission_caches(server_id, user_id)

    def _validate_server_name(self, name: str) -> str:
        if not name or not name.strip():
            raise InvalidServerNameError("Server name cannot be empty")

        try:
            name = reject_control_characters(name, "Server name")
        except HardeningViolation as exc:
            raise InvalidServerNameError(str(exc), name) from exc

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
        """Resolve a member's roles, memoized by identity and RBAC epoch.

        This is consulted repeatedly while resolving permissions and hierarchy
        for one request. The bounded, epoch-keyed L0 collapses those repeated
        joins; a miss or any cache error recomputes from the authoritative
        tables. The key includes server, user and the shared RBAC epoch, so no
        member's roles can be served for another and a revoked role becomes
        unreachable the moment its mutation bumps the epoch.
        """
        from .member_role_rows_cache import (
            _MISS as _ROLE_ROWS_MISS,
            get_cached_member_role_rows,
            prime_member_role_rows,
        )

        sid, uid = int(server_id), int(user_id)
        cached = get_cached_member_role_rows(sid, uid)
        if cached is not _ROLE_ROWS_MISS:
            return cached

        member = self.get_member(server_id, user_id)
        if not member:
            rows: List[Dict[str, Any]] = []
        else:
            db_rows = self._db.fetch_all(
                """SELECT r.* FROM srv_roles r
                   INNER JOIN srv_member_roles mr ON r.id = mr.role_id
                   WHERE mr.member_id = ? AND r.deleted = 0""",
                (member.id,),
            )
            rows = [dict(row) for row in db_rows]

        # Freeze once and return the same immutable snapshot a cache hit
        # returns; role rows carry the permission vocabulary behind every
        # hierarchy decision, so no caller may hold a mutable handle to them.
        return prime_member_role_rows(sid, uid, rows)
