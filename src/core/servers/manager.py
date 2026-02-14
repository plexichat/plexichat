"""
Server manager - Core business logic for server operations.

Handles all server operations with proper validation, permission checks,
and database interactions.
"""

import json
import secrets
from typing import Optional, List, Dict, Any, Union

import utils.config as config
import utils.logger as logger
from src.core.base import BaseManager, SnowflakeID

from .models import (
    Server,
    Channel,
    ChannelCategory,
    Role,
    Member,
    ChannelOverride,
    Invite,
    Ban,
    AuditLogEntry,
    ChannelType,
    AuditLogAction,
    DEFAULT_EVERYONE_PERMISSIONS,
)
from .exceptions import (
    ServerError,
    ServerNotFoundError,
    ServerAccessDeniedError,
    ChannelNotFoundError,
    ChannelTypeError,
    MemberNotFoundError,
    InvalidServerNameError,
    PermissionDeniedError,
    OwnerCannotLeaveError,
)
from .schema import create_tables
from .permissions import (
    has_permission as check_permission,
)
from src.core.database import (
    cache_get,
    cache_set,
    cache_delete,
    redis_available,
    cached,
)
from src.core.database.collections import CappedDict


class ServerManager(BaseManager):
    """Core server manager handling all operations."""

    ChannelType = ChannelType

    def __init__(self, db, auth_module=None, messaging_module=None):
        """
        Initialize the server manager.

        Args:
            db: Database instance (must be connected)
            auth_module: Optional auth module for user verification
            messaging_module: Optional messaging module for channel messages
        """
        super().__init__(db, auth_module)
        self._messaging = messaging_module
        self._config = self._load_config()
        self.instance_id = secrets.token_hex(4)

        # Cache size limit from config
        max_cache = config.get("redis.cache_max_items", 1000)

        # In-memory caches with TTL (reduces DB queries significantly)
        self._member_cache = CappedDict(max_size=max_cache)
        self._permission_cache = CappedDict(max_size=max_cache)
        self._channel_cache = CappedDict(max_size=max_cache)
        self._server_owner_cache = CappedDict(max_size=max_cache)
        self._member_roles_cache = CappedDict(max_size=max_cache)
        self._cache_ttl = (
            60.0  # 60 second cache TTL for server data (longer = fewer DB queries)
        )

        create_tables(db)

        # Initialize sub-handlers for modularity
        from .handlers.audit_handler import AuditHandler
        from .handlers.channel_handler import ChannelHandler
        from .handlers.role_handler import RoleHandler
        from .handlers.member_handler import MemberHandler

        self.audit_handler = AuditHandler(self)
        self.channel_handler = ChannelHandler(self)
        self.role_handler = RoleHandler(self)
        self.member_handler = MemberHandler(self)

        logger.info("Server module initialized")

    def _cache_get(self, cache: dict, key, default=None) -> Any:
        """Get value from cache if not expired."""
        if key in cache:
            value, expires = cache[key]
            if (self._get_timestamp() / 1000.0) < expires:
                return value
            del cache[key]
        return default

    def _cache_set(self, cache: dict, key, value) -> None:
        """Set value in cache with TTL."""
        cache[key] = (value, (self._get_timestamp() / 1000.0) + self._cache_ttl)

    def _cache_invalidate(self, cache: dict, key=None) -> None:
        """Invalidate cache entry or entire cache."""
        if key is None:
            cache.clear()
        else:
            cache.pop(key, None)

    def _load_config(self) -> Dict[str, Any]:
        """Load server configuration."""
        defaults = {
            "max_servers_per_user": 100,
            "max_channels_per_server": 500,
            "max_roles_per_server": 250,
            "max_members_per_server": 250000,
            "server_name_min_length": 2,
            "server_name_max_length": 100,
            "channel_name_min_length": 1,
            "channel_name_max_length": 100,
            "role_name_min_length": 1,
            "role_name_max_length": 100,
            "invite_code_length": 8,
        }

        server_config = config.get("servers", {})
        return {**defaults, **server_config}

    def _log_audit(
        self,
        server_id: SnowflakeID,
        user_id: SnowflakeID,
        action: AuditLogAction,
        target_type: Optional[str] = None,
        target_id: Optional[SnowflakeID] = None,
        changes: Optional[Dict[str, Any]] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Log an audit entry."""
        self.audit_handler.log_audit(
            server_id, user_id, action, target_type, target_id, changes, reason
        )

    def _validate_server_name(self, name: str) -> str:
        """Validate server name."""
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

    # === Server Operations ===

    def create_server(
        self,
        owner_id: SnowflakeID,
        name: str,
        description: Optional[str] = None,
        icon_url: Optional[str] = None,
    ) -> Server:
        """Create a new server."""
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

        now = self._get_timestamp()
        server_id = self._generate_id()

        self._db.execute(
            """INSERT INTO srv_servers 
               (id, name, owner_id, description, icon_url, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (server_id, name, owner_id, description, icon_url, now, now),
        )

        # Create default @everyone role
        role_id = self._generate_id()
        self._db.execute(
            """INSERT INTO srv_roles 
               (id, server_id, name, permissions, position, is_default, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                role_id,
                server_id,
                "@everyone",
                json.dumps(DEFAULT_EVERYONE_PERMISSIONS),
                0,
                1,
                now,
                now,
            ),
        )

        # Update server with default role
        self._db.execute(
            "UPDATE srv_servers SET default_role_id = ? WHERE id = ?",
            (role_id, server_id),
        )

        logger.info(
            f"[Instance:{self.instance_id}] create_server: sid={server_id}, owner={owner_id}, role_id={role_id}"
        )

        # Add owner as member
        member_id = self._generate_id()
        self._db.execute(
            """INSERT INTO srv_members 
               (id, server_id, user_id, joined_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (member_id, server_id, owner_id, now, now),
        )

        # Assign default role to owner
        mr_id = self._generate_id()
        self._db.execute(
            """INSERT INTO srv_member_roles (id, member_id, role_id, assigned_at)
               VALUES (?, ?, ?, ?)""",
            (mr_id, member_id, role_id, now),
        )

        logger.info(
            f"create_server: sid={server_id}, owner={owner_id}, member_id={member_id}"
        )

        # Invalidate membership and role caches for the owner
        self._cache_invalidate(self._member_cache, (server_id, owner_id))
        self._cache_invalidate(self._member_roles_cache, (server_id, owner_id))
        self._cache_invalidate(self._permission_cache, (owner_id, server_id, None))

        # Also invalidate server list for the user in Redis if available
        if redis_available():
            cache_delete(f"user_servers:{owner_id}")
            cache_delete(f"server:{server_id}")
            cache_delete(f"server_channels:{server_id}")

        # Create default general channel
        channel_id = self._generate_id()
        self._db.execute(
            """INSERT INTO srv_channels 
               (id, server_id, name, channel_type, position, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (channel_id, server_id, "general", ChannelType.TEXT.value, 0, now, now),
        )

        # Create conversation for the channel if messaging module available
        if self._messaging:
            conv = (
                self._messaging.create_server_channel_conversation(
                    server_id, channel_id
                )
                if hasattr(self._messaging, "create_server_channel_conversation")
                else None
            )
            if conv:
                self._db.execute(
                    "UPDATE srv_channels SET conversation_id = ? WHERE id = ?",
                    (conv.id, channel_id),
                )

        # Update server with system channel
        self._db.execute(
            "UPDATE srv_servers SET system_channel_id = ? WHERE id = ?",
            (channel_id, server_id),
        )

        self._log_audit(server_id, owner_id, AuditLogAction.SERVER_CREATE)

        logger.debug(f"Created server {server_id} for owner {owner_id}")

        result = self.get_server(server_id, owner_id)
        assert result is not None  # Should exist since we just created it
        return result

    def get_server(
        self, server_id: SnowflakeID, user_id: SnowflakeID
    ) -> Optional[Server]:
        """Get a server by ID if user has access (cached for 5 minutes)."""
        logger.info(
            f"[Instance:{self.instance_id}] get_server: sid={server_id}, uid={user_id}"
        )
        is_member = self._is_member(server_id, user_id)
        if not is_member:
            logger.warning(
                f"get_server: user {user_id} is NOT a member of server {server_id}"
            )
            return None

        # Try cache first
        cache_key = f"server:{server_id}"
        if redis_available():
            cached = cache_get(cache_key)
            if cached:
                logger.debug(f"get_server: cache hit for {server_id}")
                return self._dict_to_server(cached)

        row = self._db.fetch_one(
            """SELECT s.*,
                      (SELECT COUNT(*) FROM srv_members WHERE server_id = s.id) as member_count,
                      (SELECT COUNT(*) FROM srv_channels WHERE server_id = s.id AND deleted = 0) as channel_count,
                      (SELECT COUNT(*) FROM srv_roles WHERE server_id = s.id AND deleted = 0) as role_count
               FROM srv_servers s
               WHERE s.id = ? AND s.deleted = 0""",
            (server_id,),
        )

        if not row:
            logger.debug(
                f"get_server: server {server_id} NOT FOUND in database (or deleted)"
            )
            return None
        server = self._row_to_server(row)

        # Cache the server data (5 minute TTL)
        if redis_available():
            cache_set(cache_key, self._server_to_dict(server), ttl=300)

        return server

    @cached(ttl=15, prefix="user_servers")
    def get_servers(self, user_id: SnowflakeID, limit: int = 100) -> List[Server]:
        """Get all servers a user is a member of with optimized count fetching (cached)."""
        limit = min(limit, 200)

        # Optimized query using JOINs and GROUP BY instead of scalar subqueries
        # This significantly reduces latency for users in multiple servers
        query = """
            SELECT s.*, 
                   COUNT(DISTINCT m2.id) as member_count,
                   COUNT(DISTINCT c.id) as channel_count,
                   COUNT(DISTINCT r.id) as role_count
            FROM srv_servers s
            INNER JOIN srv_members m1 ON s.id = m1.server_id
            LEFT JOIN srv_members m2 ON s.id = m2.server_id
            LEFT JOIN srv_channels c ON s.id = c.server_id AND c.deleted = 0
            LEFT JOIN srv_roles r ON s.id = r.server_id AND r.deleted = 0
            WHERE m1.user_id = ? AND s.deleted = 0
            GROUP BY s.id
            ORDER BY s.name
            LIMIT ?
        """
        rows = self._db.fetch_all(query, (user_id, limit))

        return [self._row_to_server(row) for row in rows]

    def update_server(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        name: Optional[str] = None,
        description: Optional[str] = None,
        icon_url: Optional[str] = None,
        default_channel_id: Optional[SnowflakeID] = None,
    ) -> Server:
        """Update server settings."""
        server = self.get_server(server_id, user_id)
        if not server:
            raise ServerNotFoundError("Server not found")

        self.require_permission(user_id, server_id, "server.manage")

        updates = []
        params = []
        changes = {}

        if name is not None:
            name = self._validate_server_name(name)
            updates.append("name = ?")
            params.append(name)
            changes["name"] = {"old": server.name, "new": name}

        if description is not None:
            updates.append("description = ?")
            params.append(description)
            changes["description"] = {"old": server.description, "new": description}

        if icon_url is not None:
            updates.append("icon_url = ?")
            params.append(icon_url)
            changes["icon_url"] = {"old": server.icon_url, "new": icon_url}

        if default_channel_id is not None:
            # Verify the channel exists and belongs to this server
            if default_channel_id != 0:
                channel = self._db.fetch_one(
                    "SELECT 1 FROM srv_channels WHERE id = ? AND server_id = ? AND deleted = 0",
                    (default_channel_id, server_id),
                )
                if not channel:
                    raise ChannelNotFoundError(
                        "Default channel not found in this server"
                    )
            updates.append("default_channel_id = ?")
            params.append(default_channel_id if default_channel_id != 0 else None)
            changes["default_channel_id"] = {
                "old": server.default_channel_id,
                "new": default_channel_id,
            }

        if updates:
            updates.append("updated_at = ?")
            params.append(self._get_timestamp())
            params.append(server_id)

            self._db.execute(
                f"UPDATE srv_servers SET {', '.join(updates)} WHERE id = ?",
                tuple(params),
            )

            self._log_audit(
                server_id,
                user_id,
                AuditLogAction.SERVER_UPDATE,
                "server",
                server_id,
                changes,
            )

            # Invalidate server cache
            if redis_available():
                cache_delete(f"server:{server_id}")

        result = self.get_server(server_id, user_id)
        assert result is not None  # Should exist since we just updated it
        return result

    def delete_server(self, user_id: SnowflakeID, server_id: SnowflakeID) -> bool:
        """Delete a server (owner only)."""
        server = self.get_server(server_id, user_id)
        if not server:
            raise ServerNotFoundError("Server not found")

        if server.owner_id != user_id:
            raise ServerAccessDeniedError("Only the owner can delete the server")

        now = self._get_timestamp()
        self._db.execute(
            "UPDATE srv_servers SET deleted = 1, deleted_at = ? WHERE id = ?",
            (now, server_id),
        )

        # Invalidate server cache
        if redis_available():
            cache_delete(f"server:{server_id}")

        self._log_audit(server_id, user_id, AuditLogAction.SERVER_DELETE)

        logger.debug(f"Deleted server {server_id}")
        return True

    def transfer_ownership(
        self, user_id: SnowflakeID, server_id: SnowflakeID, new_owner_id: SnowflakeID
    ) -> Server:
        """Transfer server ownership to another member."""
        server = self.get_server(server_id, user_id)
        if not server:
            raise ServerNotFoundError("Server not found")

        if server.owner_id != user_id:
            raise ServerAccessDeniedError("Only the owner can transfer ownership")

        if not self._is_member(server_id, new_owner_id):
            raise MemberNotFoundError("New owner must be a member of the server")

        self._db.execute(
            "UPDATE srv_servers SET owner_id = ?, updated_at = ? WHERE id = ?",
            (new_owner_id, self._get_timestamp(), server_id),
        )

        self._log_audit(
            server_id,
            user_id,
            AuditLogAction.SERVER_UPDATE,
            "server",
            server_id,
            {"owner_id": {"old": user_id, "new": new_owner_id}},
        )

        updated_server = self.get_server(server_id, new_owner_id)
        assert updated_server is not None
        return updated_server

    # === Channel Operations ===

    def create_channel(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        name: str,
        channel_type: Union[ChannelType, str] = ChannelType.TEXT,
        category_id: Optional[SnowflakeID] = None,
        topic: Optional[str] = None,
        nsfw: bool = False,
        slowmode_seconds: int = 0,
        read_receipts_enabled: bool = True,
    ) -> Channel:
        """Create a new channel in a server."""
        return self.channel_handler.create_channel(
            user_id,
            server_id,
            name,
            channel_type,
            category_id,
            topic,
            nsfw,
            slowmode_seconds,
            read_receipts_enabled,
        )

    def create_category(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        name: str,
    ) -> ChannelCategory:
        """Create a new channel category."""
        return self.channel_handler.create_category(user_id, server_id, name)

    def delete_category(self, user_id: SnowflakeID, category_id: SnowflakeID) -> bool:
        """Delete a channel category."""
        return self.channel_handler.delete_category(user_id, category_id)

    def get_channel(
        self, channel_id: SnowflakeID, user_id: SnowflakeID
    ) -> Optional[Channel]:
        """Get a channel by ID if user has access (cached)."""
        cache_key = channel_id
        cached_row = self._cache_get(self._channel_cache, cache_key)

        if cached_row is None:
            row = self._db.fetch_one(
                "SELECT * FROM srv_channels WHERE id = ? AND deleted = 0",
                (channel_id,),
            )
            if not row:
                logger.debug(
                    f"get_channel: channel {channel_id} NOT FOUND in database (or deleted)"
                )
                return None
            self._cache_set(self._channel_cache, cache_key, dict(row))
            cached_row = dict(row)

        server_id = cached_row["server_id"]

        if not self._is_member(server_id, user_id):
            logger.warning(
                f"get_channel: user {user_id} is NOT a member of server {server_id}"
            )
            return None

        if not self.has_permission(user_id, server_id, "channels.view", channel_id):
            logger.warning(
                f"get_channel: user {user_id} does NOT have channels.view permission for channel {channel_id}"
            )
            return None

        return self._row_to_channel(cached_row)

    def get_channels(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        channel_type: Optional[ChannelType] = None,
    ) -> List[Channel]:
        """Get all channels in a server."""
        return self.channel_handler.get_channels(user_id, server_id, channel_type)

    def update_channel(
        self,
        user_id: SnowflakeID,
        channel_id: SnowflakeID,
        name: Optional[str] = None,
        topic: Optional[str] = None,
        nsfw: Optional[bool] = None,
        slowmode_seconds: Optional[int] = None,
        read_receipts_enabled: Optional[bool] = None,
        category_id: Optional[SnowflakeID] = None,
    ) -> Channel:
        """Update channel settings."""
        return self.channel_handler.update_channel(
            user_id,
            channel_id,
            name,
            topic,
            nsfw,
            slowmode_seconds,
            read_receipts_enabled,
            category_id,
        )

    def delete_channel(self, user_id: SnowflakeID, channel_id: SnowflakeID) -> bool:
        """Delete a channel."""
        return self.channel_handler.delete_channel(user_id, channel_id)

    def move_channel(
        self, user_id: SnowflakeID, channel_id: SnowflakeID, position: int
    ) -> Channel:
        """Move a channel to a new position."""
        return self.channel_handler.move_channel(user_id, channel_id, position)

    # === Role Operations ===

    def create_role(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        name: str,
        permissions: Optional[Dict[str, bool]] = None,
        color: Optional[str] = None,
        hoist: bool = False,
        mentionable: bool = False,
    ) -> Role:
        """Create a new role in a server."""
        return self.role_handler.create_role(
            user_id, server_id, name, permissions, color, hoist, mentionable
        )

    def get_role(self, role_id: SnowflakeID, user_id: SnowflakeID) -> Optional[Role]:
        """Get a role by ID."""
        return self.role_handler.get_role(role_id, user_id)

    def get_roles(self, user_id: SnowflakeID, server_id: SnowflakeID) -> List[Role]:
        """Get all roles in a server."""
        return self.role_handler.get_roles(user_id, server_id)

    def update_role(
        self,
        user_id: SnowflakeID,
        role_id: SnowflakeID,
        name: Optional[str] = None,
        permissions: Optional[Dict[str, bool]] = None,
        color: Optional[str] = None,
        hoist: Optional[bool] = None,
        mentionable: Optional[bool] = None,
    ) -> Role:
        """Update role settings."""
        return self.role_handler.update_role(
            user_id, role_id, name, permissions, color, hoist, mentionable
        )

    def delete_role(self, user_id: SnowflakeID, role_id: SnowflakeID) -> bool:
        """Delete a role."""
        return self.role_handler.delete_role(user_id, role_id)

    def move_role(
        self, user_id: SnowflakeID, role_id: SnowflakeID, position: int
    ) -> Role:
        """Move a role to a new position in hierarchy."""
        return self.role_handler.move_role(user_id, role_id, position)

    # === Member Operations ===

    def add_member(
        self,
        server_id: SnowflakeID,
        user_id: SnowflakeID,
        inviter_id: Optional[SnowflakeID] = None,
    ) -> Member:
        """Add a user as a member of a server."""
        return self.member_handler.add_member(server_id, user_id, inviter_id)

    def get_member(
        self, server_id: SnowflakeID, user_id: SnowflakeID
    ) -> Optional[Member]:
        """Get a member by user ID."""
        return self.member_handler.get_member(server_id, user_id)

    @cached(ttl=30, prefix="server_members")
    def get_members(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        limit: int = 100,
        after_id: Optional[SnowflakeID] = None,
    ) -> List[Member]:
        """Get members of a server."""
        if not self._is_member(server_id, user_id):
            raise ServerAccessDeniedError("Not a member of this server")

        limit = min(limit, 1000)

        query = "SELECT * FROM srv_members WHERE server_id = ?"
        params = [server_id]

        if after_id:
            query += " AND id > ?"
            params.append(after_id)

        query += " ORDER BY joined_at LIMIT ?"
        params.append(limit)

        rows = self._db.fetch_all(query, tuple(params))
        if not rows:
            return []

        # Bulk fetch roles for all members to avoid N+1
        member_ids = [row["id"] for row in rows]
        placeholders = ",".join("?" for _ in member_ids)
        role_rows = self._db.fetch_all(
            f"SELECT member_id, role_id FROM srv_member_roles WHERE member_id IN ({placeholders})",
            tuple(member_ids)
        )
        
        # Map roles to members
        roles_map = {}
        for rr in role_rows:
            mid = rr["member_id"]
            if mid not in roles_map:
                roles_map[mid] = []
            roles_map[mid].append(rr["role_id"])

        return [self._row_to_member(row, roles=roles_map.get(row["id"], [])) for row in rows]

    def get_member_user_ids(
        self,
        server_id: SnowflakeID,
        exclude_user_id: Optional[SnowflakeID] = None,
    ) -> List[SnowflakeID]:
        """
        Get just the user IDs of server members (optimized for typing/presence dispatch).

        This is faster than get_members() as it only fetches user_id column.
        """
        query = "SELECT user_id FROM srv_members WHERE server_id = ?"
        params: List[SnowflakeID] = [server_id]

        if exclude_user_id:
            query += " AND user_id != ?"
            params.append(exclude_user_id)

        rows = self._db.fetch_all(query, tuple(params))
        return [row["user_id"] for row in rows]

    def get_all_shared_member_ids(self, user_id: SnowflakeID) -> List[SnowflakeID]:
        """
        Get all unique user IDs that share at least one server with the given user.

        Args:
            user_id: ID of the user to find shared members for

        Returns:
            List of unique user IDs
        """
        rows = self._db.fetch_all(
            """SELECT DISTINCT m2.user_id 
               FROM srv_members m1
               JOIN srv_members m2 ON m1.server_id = m2.server_id
               WHERE m1.user_id = ? AND m2.user_id != ?""",
            (user_id, user_id),
        )
        return [row["user_id"] for row in rows]

    def update_member(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        member_user_id: SnowflakeID,
        nickname: Optional[str] = None,
        muted: Optional[bool] = None,
        deafened: Optional[bool] = None,
    ) -> Member:
        """Update member settings."""
        return self.member_handler.update_member(
            user_id, server_id, member_user_id, nickname, muted, deafened
        )

    def remove_member(self, user_id: SnowflakeID, server_id: SnowflakeID) -> bool:
        """Remove yourself from a server (leave)."""
        server = self.get_server(server_id, user_id)
        if not server:
            raise ServerNotFoundError("Server not found")

        if server.owner_id == user_id:
            raise OwnerCannotLeaveError(
                "Owner cannot leave. Transfer ownership first or delete the server."
            )

        member = self.get_member(server_id, user_id)
        if not member:
            raise MemberNotFoundError("Not a member of this server")

        # Remove member roles
        self._db.execute(
            "DELETE FROM srv_member_roles WHERE member_id = ?",
            (member.id,),
        )

        # Remove member
        self._db.execute(
            "DELETE FROM srv_members WHERE server_id = ? AND user_id = ?",
            (server_id, user_id),
        )

        # Remove from messaging conversations if messaging module available
        if self._messaging:
            try:
                channels = self._db.fetch_all(
                    "SELECT conversation_id FROM srv_channels WHERE server_id = ? AND deleted = 0",
                    (server_id,)
                )
                conv_ids = [ch["conversation_id"] for ch in channels if ch["conversation_id"]]
                
                if conv_ids:
                    if hasattr(self._messaging, "remove_participant_from_conversations"):
                        self._messaging.remove_participant_from_conversations(user_id, conv_ids)
            except Exception as e:
                logger.error(f"Error removing member {user_id} from server conversations: {e}")

        # Invalidate caches for the user leaving
        self._cache_invalidate(self._member_cache, (server_id, user_id))
        self._cache_invalidate(self._member_cache, f"is_member:{server_id}:{user_id}")
        self._cache_invalidate(self._member_roles_cache, (server_id, user_id))
        self._cache_invalidate(self._permission_cache, (user_id, server_id, None))

        # Invalidate Redis
        from src.core.database import cache_delete, invalidate_pattern
        cache_delete(f"is_member:{server_id}:{user_id}")
        invalidate_pattern(f"perms:{user_id}:{server_id}:*")
        invalidate_pattern(f"member_data:*{user_id}*")
        self.get_servers.invalidate(user_id)  # type: ignore

        # Also invalidate any channel-specific permission caches
        for key in list(self._permission_cache.keys()):
            if key[0] == user_id and key[1] == server_id:
                self._cache_invalidate(self._permission_cache, key)

        self._log_audit(
            server_id, user_id, AuditLogAction.MEMBER_LEAVE, "member", user_id
        )

        return True

    def kick_member(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        member_user_id: SnowflakeID,
        reason: Optional[str] = None,
    ) -> bool:
        """Kick a member from a server."""
        return self.member_handler.kick_member(user_id, server_id, member_user_id, reason)

    def ban_member(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        member_user_id: SnowflakeID,
        reason: Optional[str] = None,
        delete_message_days: int = 0,
    ) -> Ban:
        """Ban a user from a server."""
        return self.member_handler.ban_member(user_id, server_id, member_user_id, reason)

    def unban_member(
        self, user_id: SnowflakeID, server_id: SnowflakeID, banned_user_id: SnowflakeID
    ) -> bool:
        """Unban a user from a server."""
        return self.member_handler.unban_member(user_id, server_id, banned_user_id)

    def get_bans(self, user_id: SnowflakeID, server_id: SnowflakeID) -> List[Ban]:
        """Get all bans for a server."""
        return self.member_handler.get_bans(user_id, server_id)

    # === Role Assignment ===

    def assign_role(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        member_user_id: SnowflakeID,
        role_id: SnowflakeID,
    ) -> bool:
        """Assign a role to a member."""
        return self.role_handler.assign_role(user_id, server_id, member_user_id, role_id)

    def remove_role(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        member_user_id: SnowflakeID,
        role_id: SnowflakeID,
    ) -> bool:
        """Remove a role from a member."""
        return self.role_handler.remove_role(user_id, server_id, member_user_id, role_id)

    def get_member_roles(
        self, server_id: SnowflakeID, member_user_id: SnowflakeID
    ) -> List[Role]:
        """Get all roles assigned to a member."""
        return self.role_handler.get_member_roles(server_id, member_user_id)

    # === Permission Operations ===

    def get_channel_override(
        self,
        channel_id: SnowflakeID,
        target_type: str,
        target_id: SnowflakeID,
    ) -> Optional[ChannelOverride]:
        """Get permission override for a channel."""
        return self.role_handler.get_channel_override(channel_id, target_type, target_id)

    def set_channel_override(
        self,
        user_id: SnowflakeID,
        channel_id: SnowflakeID,
        target_type: str,
        target_id: SnowflakeID,
        allow: Optional[Dict[str, bool]] = None,
        deny: Optional[Dict[str, bool]] = None,
    ) -> ChannelOverride:
        """Set permission override for a channel."""
        return self.role_handler.set_channel_override(user_id, channel_id, target_type, target_id, allow, deny)

    def delete_channel_override(
        self,
        user_id: SnowflakeID,
        channel_id: SnowflakeID,
        target_type: str,
        target_id: SnowflakeID,
    ) -> bool:
        """Delete a permission override."""
        return self.role_handler.delete_channel_override(user_id, channel_id, target_type, target_id)

    def has_permission(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        permission: str,
        channel_id: Optional[SnowflakeID] = None,
    ) -> bool:
        """Check if a user has a permission in a server/channel."""
        return self.role_handler.has_permission(user_id, server_id, permission, channel_id)

    def get_permissions(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        channel_id: Optional[SnowflakeID] = None,
    ) -> Dict[str, bool]:
        """Get all permissions for a user in a server/channel (cached)."""
        return self.role_handler.get_permissions(user_id, server_id, channel_id)

    def require_permission(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        permission: str,
        channel_id: Optional[SnowflakeID] = None,
    ) -> None:
        """Require a permission, raising if not granted."""
        if not self.has_permission(user_id, server_id, permission, channel_id):
            raise PermissionDeniedError(
                f"Missing required permission: {permission}", permission
            )

    # === Invite Operations ===

    def create_invite(
        self,
        user_id: SnowflakeID,
        channel_id: SnowflakeID,
        max_age: int = 86400,
        max_uses: int = 0,
        temporary: bool = False,
    ) -> Invite:
        """Create an invite to a channel."""
        return self.member_handler.create_invite(user_id, channel_id, max_age, max_uses, temporary)

    def get_invite(self, code: str) -> Optional[Invite]:
        """Get an invite by code."""
        return self.member_handler.get_invite(code)

    def get_invites(self, user_id: SnowflakeID, server_id: SnowflakeID) -> List[Invite]:
        """Get all invites for a server."""
        server = self.get_server(server_id, user_id)
        if not server:
            raise ServerNotFoundError("Server not found")

        self.require_permission(user_id, server_id, "invites.manage")

        rows = self._db.fetch_all(
            "SELECT * FROM srv_invites WHERE server_id = ? AND revoked = 0 ORDER BY created_at DESC",
            (server_id,),
        )

        return [self._row_to_invite(row) for row in rows]

    def get_server_invites(
        self, user_id: SnowflakeID, server_id: SnowflakeID
    ) -> List[Invite]:
        """Alias for get_invites."""
        return self.get_invites(user_id, server_id)

    def use_invite(self, user_id: SnowflakeID, code: str) -> Member:
        """Use an invite to join a server."""
        return self.member_handler.use_invite(user_id, code)

    def delete_invite(self, user_id: SnowflakeID, code: str) -> bool:
        """Delete an invite."""
        return self.member_handler.delete_invite(user_id, code)

    # === Channel Messaging ===

    def send_channel_message(
        self,
        user_id: SnowflakeID,
        channel_id: SnowflakeID,
        content: str,
        attachments: Optional[List[Dict[str, Any]]] = None,
        reply_to_id: Optional[SnowflakeID] = None,
    ) -> Any:
        """Send a message to a text channel."""
        channel = self.get_channel(channel_id, user_id)
        if not channel:
            raise ChannelNotFoundError("Channel not found")

        if channel.channel_type != ChannelType.TEXT:
            raise ChannelTypeError("Can only send messages to text channels")

        # Check permissions
        permissions = self.get_permissions(user_id, channel.server_id, channel_id)
        if not check_permission(permissions, "messages.send"):
            raise PermissionDeniedError("Missing messages.send permission", "messages.send")

        # Enforce slowmode
        if channel.slowmode_seconds > 0:
            # Bypass slowmode if user has bypass permission or management perms
            can_bypass = check_permission(permissions, "messages.bypass_slowmode") or \
                         check_permission(permissions, "messages.manage") or \
                         check_permission(permissions, "channels.manage") or \
                         check_permission(permissions, "administrator")
            
            if not can_bypass:
                retry_after = self._check_slowmode(user_id, channel_id, channel.slowmode_seconds)
                if retry_after:
                    from src.core.ratelimit.exceptions import RateLimitError
                    raise RateLimitError(f"Slowmode is enabled. Try again in {retry_after:.1f}s", retry_after)

        if not self._messaging:
            raise ServerError("Messaging module not available")

        # Use the channel's conversation
        if not channel.conversation_id:
            raise ServerError("Channel has no associated conversation")

        return self._messaging.send_message(
            user_id=user_id,
            conversation_id=channel.conversation_id,
            content=content,
            reply_to_id=reply_to_id,
            attachments=attachments,
        )

    def _check_slowmode(self, user_id: SnowflakeID, channel_id: SnowflakeID, slowmode_seconds: int) -> Optional[float]:
        """Check if user is slowmoded in channel. Returns retry_after if limited."""
        if slowmode_seconds <= 0:
            return None
            
        key = f"slowmode:{channel_id}:{user_id}"
        # Use Redis if available
        if redis_available():
            try:
                from src.core.database import cache_get, cache_set
                last_msg_time = cache_get(key)
                now = self._get_timestamp() / 1000.0
                if last_msg_time:
                    try:
                        elapsed = now - float(last_msg_time)
                        if elapsed < slowmode_seconds:
                            return slowmode_seconds - elapsed
                    except (ValueError, TypeError):
                        pass
                cache_set(key, str(now), ttl=slowmode_seconds)
            except Exception as e:
                logger.debug(f"Slowmode check failed (Redis error): {e}")
        return None

    def get_channel_messages(
        self,
        user_id: SnowflakeID,
        channel_id: SnowflakeID,
        limit: int = 50,
        before_id: Optional[SnowflakeID] = None,
        after_id: Optional[SnowflakeID] = None,
    ) -> List[Any]:
        """Get messages from a text channel."""
        channel = self.get_channel(channel_id, user_id)
        if not channel:
            raise ChannelNotFoundError("Channel not found")

        if channel.channel_type != ChannelType.TEXT:
            raise ChannelTypeError("Can only get messages from text channels")

        self.require_permission(user_id, channel.server_id, "messages.read", channel_id)

        if not self._messaging:
            raise ServerError("Messaging module not available")

        if not channel.conversation_id:
            return []

        messages = self._messaging.get_messages(
            user_id=user_id,
            conversation_id=channel.conversation_id,
            limit=limit,
            before_id=before_id,
            after_id=after_id,
        )
        
        return messages

    # === Audit Log ===

    def get_audit_log(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        limit: int = 50,
        action_type: Optional[AuditLogAction] = None,
        before_id: Optional[SnowflakeID] = None,
    ) -> List[AuditLogEntry]:
        """Get audit log entries for a server."""
        server = self.get_server(server_id, user_id)
        if not server:
            raise ServerNotFoundError("Server not found")

        self.require_permission(user_id, server_id, "server.view_audit_log")

        limit = min(limit, 100)

        query = "SELECT * FROM srv_audit_log WHERE server_id = ?"
        params: List[SnowflakeID | str | int] = [server_id]

        if action_type:
            query += " AND action = ?"
            params.append(action_type.value)

        if before_id:
            query += " AND id < ?"
            params.append(before_id)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = self._db.fetch_all(query, tuple(params))

        return [self._row_to_audit_entry(row) for row in rows]

    # === Helper Methods ===

    def _is_member(self, server_id: SnowflakeID, user_id: SnowflakeID) -> bool:
        """Check if user is a member of server (cached in Redis)."""
        # Ensure we are working with ints
        try:
            sid = int(server_id)
            uid = int(user_id)
        except (ValueError, TypeError):
            return False

        cache_key = f"is_member:{sid}:{uid}"
        
        # 1. Try internal memory first (fastest)
        mem_cached = self._cache_get(self._member_cache, cache_key)
        if mem_cached is not None:
            return mem_cached

        # 2. Try Redis (shared across workers)
        if redis_available():
            redis_cached = cache_get(cache_key)
            if redis_cached is not None:
                is_member = bool(int(redis_cached))
                self._cache_set(self._member_cache, cache_key, is_member)
                return is_member

        # 3. Check if user is the owner (from server table)
        # Check owner cache (internal memory)
        owner_id = self._cache_get(self._server_owner_cache, sid)
        if owner_id is None:
            # Check Redis for owner
            owner_cache_key = f"server_owner:{sid}"
            if redis_available():
                owner_id_cached = cache_get(owner_cache_key)
                if owner_id_cached:
                    owner_id = int(owner_id_cached)
                    self._cache_set(self._server_owner_cache, sid, owner_id)

            if owner_id is None:
                row = self._db.fetch_one(
                    "SELECT owner_id FROM srv_servers WHERE id = ? AND deleted = 0",
                    (sid,),
                )
                if row:
                    owner_id = int(row["owner_id"])
                    self._cache_set(self._server_owner_cache, sid, owner_id)
                    if redis_available():
                        cache_set(owner_cache_key, str(owner_id), ttl=3600)

        if owner_id == uid:
            self._cache_set(self._member_cache, cache_key, True)
            if redis_available():
                cache_set(cache_key, "1", ttl=300)
            return True

        # 4. Final DB check
        row = self._db.fetch_one(
            "SELECT 1 FROM srv_members WHERE server_id = ? AND user_id = ?",
            (sid, uid),
        )

        is_member = row is not None
        
        # Cache result
        self._cache_set(self._member_cache, cache_key, is_member)
        if redis_available():
            cache_set(cache_key, "1" if is_member else "0", ttl=300)
            
        return is_member

    def _server_exists(self, server_id: SnowflakeID) -> bool:
        """Check if a server exists."""
        row = self._db.fetch_one(
            "SELECT 1 FROM srv_servers WHERE id = ? AND deleted = 0",
            (server_id,),
        )
        return row is not None

    def _get_member_role_rows(
        self, server_id: SnowflakeID, user_id: SnowflakeID
    ) -> List[Dict[str, Any]]:
        """Get raw role rows for a member."""
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

    # === Row Converters ===

    def _row_to_server(self, row: Dict[str, Any]) -> Server:
        """Convert database row to Server model."""
        sid = row["id"]
        owner_id = row["owner_id"]
        logger.debug(f"_row_to_server: sid={sid}, owner={owner_id}")

        # Handle both dict and sqlite3.Row
        member_count = 0
        channel_count = 0
        role_count = 0
        try:
            member_count = row["member_count"]
        except (KeyError, IndexError):
            pass
        try:
            channel_count = row["channel_count"]
        except (KeyError, IndexError):
            pass
        try:
            role_count = row["role_count"]
        except (KeyError, IndexError):
            pass

        # Handle default_channel_id which may not exist in older databases
        default_channel_id = None
        try:
            default_channel_id = row["default_channel_id"]
        except (KeyError, IndexError):
            pass

        return Server(
            id=row["id"],
            name=row["name"],
            owner_id=row["owner_id"],
            description=row["description"],
            icon_path=row["icon_url"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            member_count=member_count,
            channel_count=channel_count,
            role_count=role_count,
            default_role_id=row["default_role_id"],
            default_channel_id=default_channel_id,
            system_channel_id=row["system_channel_id"],
            verification_level=row["verification_level"],
            deleted=bool(row["deleted"]),
            deleted_at=row["deleted_at"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else None,
        )

    def _server_to_dict(self, server: Server) -> Dict[str, Any]:
        """Convert Server model to dict for caching."""
        return {
            "id": server.id,
            "name": server.name,
            "owner_id": server.owner_id,
            "description": server.description,
            "icon_url": server.icon_url,
            "created_at": server.created_at,
            "updated_at": server.updated_at,
            "member_count": server.member_count,
            "channel_count": server.channel_count,
            "role_count": server.role_count,
            "default_role_id": server.default_role_id,
            "default_channel_id": server.default_channel_id,
            "system_channel_id": server.system_channel_id,
            "verification_level": server.verification_level,
            "deleted": server.deleted,
            "deleted_at": server.deleted_at,
            "metadata": server.metadata,
        }

    def _dict_to_server(self, data: Dict[str, Any]) -> Server:
        """Convert cached dict to Server model."""
        return Server(
            id=data["id"],
            name=data["name"],
            owner_id=data["owner_id"],
            description=data.get("description"),
            icon_path=data.get("icon_url"),
            banner_path=data.get("banner_url"),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            member_count=data.get("member_count", 0),
            channel_count=data.get("channel_count", 0),
            role_count=data.get("role_count", 0),
            default_role_id=data.get("default_role_id"),
            default_channel_id=data.get("default_channel_id"),
            system_channel_id=data.get("system_channel_id"),
            verification_level=data.get("verification_level", 0),
            deleted=data.get("deleted", False),
            deleted_at=data.get("deleted_at"),
            metadata=data.get("metadata"),
        )

    def _row_to_channel(self, row: Dict[str, Any]) -> Channel:
        """Convert database row to Channel model."""
        return Channel(
            id=row["id"],
            server_id=row["server_id"],
            name=row["name"],
            channel_type=ChannelType(row["channel_type"]),
            category_id=row["category_id"],
            position=row["position"],
            topic=row["topic"],
            nsfw=bool(row["nsfw"]),
            slowmode_seconds=row.get("slowmode_seconds", 0),
            read_receipts_enabled=bool(row.get("read_receipts_enabled", True)),
            conversation_id=row.get("conversation_id"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            deleted=bool(row.get("deleted", False)),
            deleted_at=row.get("deleted_at"),
            metadata=json.loads(row["metadata"]) if row.get("metadata") else None,
        )

    def _row_to_category(self, row: Dict[str, Any]) -> ChannelCategory:
        """Convert database row to ChannelCategory model."""
        return ChannelCategory(
            id=row["id"],
            server_id=row["server_id"],
            name=row["name"],
            position=row["position"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_role(self, row: Dict[str, Any]) -> Role:
        """Convert database row to Role model."""
        perms = row["permissions"]
        if isinstance(perms, str):
            perms = json.loads(perms) if perms else {}

        return Role(
            id=row["id"],
            server_id=row["server_id"],
            name=row["name"],
            permissions=perms or {},
            color=row["color"],
            hoist=bool(row["hoist"]),
            mentionable=bool(row["mentionable"]),
            position=row["position"],
            is_default=bool(row.get("is_default", False)),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            deleted=bool(row.get("deleted", False)),
        )

    def _row_to_member(
        self,
        row: Dict[str, Any],
        roles: Optional[List[SnowflakeID]] = None,
        username: Optional[str] = None,
        avatar_url: Optional[str] = None,
    ) -> Member:
        """Convert database row to Member model."""
        # Get role IDs
        role_ids = roles
        if role_ids is None:
            role_rows = self._db.fetch_all(
                "SELECT role_id FROM srv_member_roles WHERE member_id = ?",
                (row["id"],),
            )
            role_ids = [r["role_id"] for r in role_rows]

        return Member(
            id=row["id"],
            server_id=row["server_id"],
            user_id=row["user_id"],
            nickname=row.get("nickname"),
            username=username,
            avatar_url=avatar_url,
            joined_at=row["joined_at"],
            updated_at=row.get("updated_at", row["joined_at"]),
            muted=bool(row.get("muted", False)),
            deafened=bool(row.get("deafened", False)),
            inviter_id=row.get("inviter_id"),
            roles=role_ids,
        )

    def _row_to_override(self, row: Dict[str, Any]) -> ChannelOverride:
        """Convert database row to ChannelOverride model."""
        allow = row["allow"]
        deny = row["deny"]

        if isinstance(allow, str):
            allow = json.loads(allow) if allow else {}
        if isinstance(deny, str):
            deny = json.loads(deny) if deny else {}

        return ChannelOverride(
            id=row["id"],
            channel_id=row["channel_id"],
            target_type=row["target_type"],
            target_id=row["target_id"],
            allow=allow or {},
            deny=deny or {},
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_invite(self, row: Dict[str, Any]) -> Invite:
        """Convert database row to Invite model."""
        return Invite(
            id=row["id"],
            code=row["code"],
            server_id=row["server_id"],
            channel_id=row["channel_id"],
            inviter_id=row["inviter_id"],
            max_age=row["max_age"],
            max_uses=row["max_uses"],
            uses=row["uses"],
            temporary=bool(row["temporary"]),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            revoked=bool(row["revoked"]),
        )

    def _row_to_ban(self, row: Dict[str, Any]) -> Ban:
        """Convert database row to Ban model."""
        return Ban(
            id=row["id"],
            server_id=row["server_id"],
            user_id=row["user_id"],
            banned_by=row["banned_by"],
            reason=row["reason"],
            created_at=row["created_at"],
        )

    def _row_to_audit_entry(self, row: Dict[str, Any]) -> AuditLogEntry:
        """Convert database row to AuditLogEntry model."""
        changes = row.get("changes")
        if isinstance(changes, str):
            try:
                changes = json.loads(changes) if changes else None
            except json.JSONDecodeError:
                changes = None

        action_val = row.get("action_type") or row.get("action")
        action_type = AuditLogAction.SERVER_UPDATE
        if action_val:
            if isinstance(action_val, str):
                action_val = action_val.lower().replace("-", "_")
            
            try:
                action_type = AuditLogAction(action_val)
            except (ValueError, KeyError):
                # Try to find by name if value doesn't match
                try:
                    action_type = AuditLogAction[action_val.upper()]
                except (ValueError, KeyError):
                    action_type = AuditLogAction.SERVER_UPDATE

        return AuditLogEntry(
            id=row["id"],
            server_id=row["server_id"],
            user_id=row["user_id"],
            action_type=action_type,
            target_type=row.get("target_type"),
            target_id=row.get("target_id"),
            changes=changes,
            reason=row.get("reason"),
            created_at=row["created_at"],
        )
