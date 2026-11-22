"""Effective permission resolution, caching and permission checks."""

import json
from typing import Optional, List, Dict, Any

from src.core.base import SnowflakeID
from src.core.database import cached

from ...channel_access_cache import get_channel_access_cache
from ...permission_utils import (
    calculate_base_permissions,
    apply_channel_overrides,
    has_permission as check_permission,
)
from .protocol import RoleHandlerProtocol


class RolePermissionMixin(RoleHandlerProtocol):
    def get_permissions_batch(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        channel_ids: List[SnowflakeID],
    ) -> Dict[SnowflakeID, Dict[str, bool]]:
        """Get permissions for multiple channels in a server.

        Overrides are fetched with at most two set-based queries (one for the
        caller's member overrides, one for all of their role overrides)
        instead of one query per ``(channel, role)`` pair.  The channel-listing
        path calls this with every channel in a server, so the old loop grew
        linearly with ``channels x roles`` on every cold cache read.
        """
        if not channel_ids:
            return {}

        # 1. Base permissions: resolved once for the whole batch.
        base_perms = self.get_permissions(user_id, server_id)

        # Administrators bypass channel overrides entirely.
        if base_perms.get("administrator"):
            return {cid: base_perms for cid in channel_ids}

        # 2. The caller's roles, used to scope and order role overrides.
        role_rows = self.manager._get_member_role_rows(server_id, user_id)
        role_ids = [r["id"] for r in role_rows]

        # Validate role_ids are integers to prevent SQL injection
        if role_ids and not all(isinstance(rid, int) for rid in role_ids):
            raise ValueError("All role IDs must be integers")

        # Role-position map so overrides can be applied LOWEST position
        # first: a higher-position role's deny must beat a lower role's
        # allow (see apply_channel_overrides ordering contract).
        role_positions = {int(r["id"]): int(r.get("position") or 0) for r in role_rows}

        # 3. Fetch every relevant override in two bounded queries.  The
        #    placeholders are generated from the number of bound parameters,
        #    never from untrusted values, and every value is bound -- the same
        #    pattern MemberOpsMixin.get_members uses for its role batch.
        channel_ph = ",".join("?" for _ in channel_ids)
        member_rows = self.db.fetch_all(
            "SELECT * FROM srv_channel_overrides "
            f"WHERE channel_id IN ({channel_ph}) "
            "AND target_type = 'member' AND target_id = ?",
            (*channel_ids, user_id),
        )
        member_overrides: Dict[SnowflakeID, Dict[str, Any]] = {
            row["channel_id"]: dict(row) for row in member_rows
        }

        channel_overrides: Dict[SnowflakeID, List[Dict[str, Any]]] = {
            cid: [] for cid in channel_ids
        }
        if role_ids:
            role_ph = ",".join("?" for _ in role_ids)
            role_override_rows = self.db.fetch_all(
                "SELECT * FROM srv_channel_overrides "
                f"WHERE channel_id IN ({channel_ph}) "
                f"AND target_type = 'role' AND target_id IN ({role_ph})",
                (*channel_ids, *role_ids),
            )
            for row in role_override_rows:
                cid = row["channel_id"]
                if cid in channel_overrides:
                    channel_overrides[cid].append(dict(row))

        # Lowest-position role first so the highest role's decision wins.
        for cid in channel_overrides:
            channel_overrides[cid].sort(
                key=lambda o: role_positions.get(int(o.get("target_id") or 0), 0)
            )

        # 4. Calculate permissions for each channel and warm per-channel cache.
        result: Dict[SnowflakeID, Dict[str, bool]] = {}
        for cid in channel_ids:
            perms = apply_channel_overrides(
                base_perms, channel_overrides[cid], member_overrides.get(cid)
            )
            result[cid] = perms

            cache_key = (user_id, server_id, cid)
            self.manager._cache_set(
                self.manager._permission_cache_prefix, cache_key, perms
            )

        return result

    def get_permissions(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        channel_id: Optional[SnowflakeID] = None,
    ) -> Dict[str, bool]:
        """Get all permissions for a user in a server/channel (cached in Valkey)."""
        cache_key = f"perms:{user_id}:{server_id}:{channel_id or 0}"

        # 1. Try internal memory first
        mem_cached = self.manager._cache_get(
            self.manager._permission_cache_prefix, cache_key
        )
        if mem_cached is not None:
            return mem_cached

        # 2. Try Valkey
        from src.core.database import cache_get, cache_set, valkey_available

        if valkey_available():
            valkey_cached = cache_get(cache_key)
            if valkey_cached is not None:
                if isinstance(valkey_cached, str):
                    perms = json.loads(valkey_cached)
                else:
                    perms = valkey_cached
                self.manager._cache_set(
                    self.manager._permission_cache_prefix, cache_key, perms
                )
                return perms

        # 3. Calculate permissions
        owner_id = self.manager._cache_get(
            self.manager._server_owner_cache_prefix, server_id
        )
        if owner_id is None:
            server_row = self.db.fetch_one(
                "SELECT owner_id FROM srv_servers WHERE id = ? AND deleted = 0",
                (server_id,),
            )
            if not server_row:
                return {}
            owner_id = int(server_row["owner_id"])
            self.manager._cache_set(
                self.manager._server_owner_cache_prefix, server_id, owner_id
            )

        is_owner = int(owner_id) == int(user_id)
        role_rows = self.manager._get_member_role_rows(server_id, user_id)
        base_perms = calculate_base_permissions(role_rows, is_owner)

        # The server owner is an administrator even if a legacy or partially
        # restored database is missing the owner's membership row. Keep the
        # base permissions in that case, but do not return early: timeout
        # restrictions below must still be evaluated.
        result = base_perms
        if channel_id:
            member = self.manager.get_member(server_id, user_id)
            if not member:
                if not is_owner:
                    return {}
                member = None
            if member is None:
                role_ids = []
                role_overrides = []
                member_override = None
            else:
                role_ids = [r["id"] for r in role_rows]
                role_overrides = []
                if role_ids:
                    if not all(isinstance(rid, int) for rid in role_ids):
                        raise ValueError("All role IDs must be integers")
                    # One set-based lookup instead of one query per role.
                    role_ph = ",".join("?" for _ in role_ids)
                    override_rows = self.db.fetch_all(
                        "SELECT * FROM srv_channel_overrides "
                        f"WHERE channel_id = ? AND target_type = 'role' "
                        f"AND target_id IN ({role_ph})",
                        (channel_id, *role_ids),
                    )
                    # Lowest-position role first so a higher role's deny
                    # beats a lower role's allow (apply_channel_overrides
                    # applies them in the order given).
                    role_positions = {
                        int(r["id"]): int(r.get("position") or 0) for r in role_rows
                    }
                    role_overrides = sorted(
                        (dict(row) for row in override_rows),
                        key=lambda o: role_positions.get(
                            int(o.get("target_id") or 0), 0
                        ),
                    )

                member_override_row = self.db.fetch_one(
                    """SELECT * FROM srv_channel_overrides
                       WHERE channel_id = ? AND target_type = 'member' AND target_id = ?""",
                    (channel_id, user_id),
                )
                member_override = (
                    dict(member_override_row) if member_override_row else None
                )

                result = apply_channel_overrides(
                    base_perms, role_overrides, member_override
                )

        # 4. Check for timeout. Channel overrides (if any) were already applied
        #    above; here we only revoke the interaction permissions of a timed-out
        #    member on top of the resolved permission set.
        # get_member() is @cached and may hand back a plain dict on cache hits,
        # so normalize before touching attributes.
        member = self.manager.get_member(server_id, user_id)
        if isinstance(member, dict):
            timeout_until = member.get("timeout_until")
        else:
            timeout_until = getattr(member, "timeout_until", None)
        if timeout_until:
            now = self.manager._get_timestamp()
            if timeout_until > now:
                # User is timed out, revoke interaction permissions
                restricted_perms = [
                    "messages.send",
                    "messages.add_reactions",
                    "voice.speak",
                    "voice.connect",
                    "messages.attach_files",
                    "messages.embed_links",
                ]
                for perm in restricted_perms:
                    result[perm] = False

        # 5. Cache result
        self.manager._cache_set(
            self.manager._permission_cache_prefix, cache_key, result
        )
        if valkey_available():
            cache_set(cache_key, result, ttl=60)

        return result

    # Role/permission mutations invalidate the whole ``has_perm:*`` verdict
    # namespace at once, and this verdict is queried from many request threads
    # simultaneously (message/poll hydration, audience + notification fan-out).
    # Without coalescing, that mass invalidation makes every thread re-run the
    # DB-backed ``get_permissions`` resolver for the same cold key -- a
    # thundering-herd availability spike. Collapse concurrent cold misses to a
    # single authoritative computation. Authorization semantics are unchanged:
    # single-flight shares one verified verdict, keyed by the full call identity.
    @cached(ttl=10, prefix="has_perm", single_flight=True)
    def _compute_has_permission(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        permission: str,
        channel_id: Optional[SnowflakeID] = None,
    ) -> bool:
        """Resolve a permission from the authoritative snapshot (cached tier)."""
        permissions = self.get_permissions(user_id, server_id, channel_id)
        return check_permission(permissions, permission)

    def has_permission(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        permission: str,
        channel_id: Optional[SnowflakeID] = None,
    ) -> bool:
        """Check if a user has a permission in a server/channel.

        Channel-scoped checks are the hot path: message-list hydration, poll
        hydration, event-audience resolution and notification fan-out all ask
        the same ``(user, channel, permission)`` question repeatedly. Answer it
        from a bounded, role-version-keyed L1 memo so a burst pays for one
        authoritative resolution. The memo is invalidated the instant any
        role/membership/override change bumps the scope's epoch (see
        ``channel_access_cache``), so a revoked permission can never be served
        from a stale grant. Server-scoped checks (no channel) keep using the
        underlying cached resolver directly.

        Any cache error degrades to the authoritative computation, which alone
        decides grant-vs-deny: a broken memo can never synthesize a grant.
        """
        if channel_id is None:
            return self._compute_has_permission(user_id, server_id, permission, None)

        cache = get_channel_access_cache()
        try:
            cached = cache.get(
                int(server_id), int(user_id), int(channel_id), permission
            )
            if cached is not None:
                return cached
        except Exception:
            # Fail closed to the authoritative resolver; never infer a grant.
            pass

        decision = bool(
            self._compute_has_permission(user_id, server_id, permission, channel_id)
        )
        try:
            cache.put(
                int(server_id), int(user_id), int(channel_id), permission, decision
            )
        except Exception:
            pass
        return decision
