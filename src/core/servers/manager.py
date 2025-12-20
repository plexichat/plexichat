"""
Server manager - Core business logic for server operations.

Handles all server operations with proper validation, permission checks,
and database interactions.
"""

import time
import json
import secrets
import string
from typing import Optional, List, Dict, Any, Tuple

import utils.config as config
import utils.logger as logger
from src.utils.encryption import generate_snowflake_id

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
    CategoryNotFoundError,
    RoleNotFoundError,
    RoleHierarchyError,
    DefaultRoleError,
    MemberNotFoundError,
    MemberExistsError,
    InviteNotFoundError,
    InviteExpiredError,
    InviteMaxUsesError,
    BanExistsError,
    UserBannedError,
    InvalidServerNameError,
    InvalidChannelNameError,
    InvalidRoleNameError,
    PermissionDeniedError,
    OwnerCannotLeaveError,
    CannotModifyOwnerError,
)
from .schema import create_tables
from .permissions import (
    calculate_base_permissions,
    apply_channel_overrides,
    has_permission as check_permission,
    can_manage_role,
    can_manage_member,
)
from src.core.database import cache_get, cache_set, cache_delete, redis_available


class ServerManager:
    """Core server manager handling all operations."""

    def __init__(self, db, auth_module=None, messaging_module=None):
        """
        Initialize the server manager.

        Args:
            db: Database instance (must be connected)
            auth_module: Optional auth module for user verification
            messaging_module: Optional messaging module for channel messages
        """
        self._db = db
        self._auth = auth_module
        self._messaging = messaging_module
        self._config = self._load_config()

        # In-memory caches with TTL (reduces DB queries significantly)
        self._member_cache: Dict[Tuple[int, int], Tuple[Any, float]] = {}
        self._permission_cache: Dict[Tuple[int, int, Optional[int]], Tuple[Dict[str, bool], float]] = {}
        self._channel_cache: Dict[int, Tuple[Any, float]] = {}
        self._server_owner_cache: Dict[int, Tuple[int, float]] = {}  # server_id -> owner_id
        self._member_roles_cache: Dict[Tuple[int, int], Tuple[list, float]] = {}  # (server_id, user_id) -> roles
        self._cache_ttl = 60.0  # 60 second cache TTL for server data (longer = fewer DB queries)

        create_tables(db)

        logger.info("Server module initialized")

    def _cache_get(self, cache: dict, key, default=None) -> Any:
        """Get value from cache if not expired."""
        if key in cache:
            value, expires = cache[key]
            if time.time() < expires:
                return value
            del cache[key]
        return default

    def _cache_set(self, cache: dict, key, value) -> None:
        """Set value in cache with TTL."""
        cache[key] = (value, time.time() + self._cache_ttl)

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

    def _get_timestamp(self) -> int:
        """Get current timestamp in milliseconds."""
        return int(time.time() * 1000)

    def _generate_id(self) -> int:
        """Generate a new Snowflake ID."""
        return generate_snowflake_id()

    def _generate_invite_code(self) -> str:
        """Generate a unique invite code."""
        length = self._config.get("invite_code_length", 8)
        chars = string.ascii_letters + string.digits
        while True:
            code = "".join(secrets.choice(chars) for _ in range(length))
            existing = self._db.fetch_one(
                "SELECT 1 FROM srv_invites WHERE code = ?", (code,)
            )
            if not existing:
                return code

    def _validate_server_name(self, name: str) -> str:
        """Validate and sanitize server name."""
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
        """Validate and sanitize channel name."""
        if not name or not name.strip():
            raise InvalidChannelNameError("Channel name cannot be empty")

        name = name.strip().lower().replace(" ", "-")
        max_len = self._config.get("channel_name_max_length", 100)

        if len(name) > max_len:
            raise InvalidChannelNameError(
                f"Channel name cannot exceed {max_len} characters", name
            )

        return name

    def _validate_role_name(self, name: str) -> str:
        """Validate and sanitize role name."""
        if not name or not name.strip():
            raise InvalidRoleNameError("Role name cannot be empty")

        name = name.strip()
        max_len = self._config.get("role_name_max_length", 100)

        if len(name) > max_len:
            raise InvalidRoleNameError(
                f"Role name cannot exceed {max_len} characters", name
            )

        return name

    def _log_audit(
        self,
        server_id: int,
        user_id: int,
        action: AuditLogAction,
        target_type: Optional[str] = None,
        target_id: Optional[int] = None,
        changes: Optional[Dict[str, Any]] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Log an audit entry."""
        entry_id = self._generate_id()
        now = self._get_timestamp()

        self._db.execute(
            """INSERT INTO srv_audit_log 
               (id, server_id, user_id, action, target_type, target_id, changes, reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry_id,
                server_id,
                user_id,
                action.value,
                target_type,
                target_id,
                json.dumps(changes) if changes else None,
                reason,
                now,
            ),
        )

    # === Server Operations ===

    def create_server(
        self,
        owner_id: int,
        name: str,
        description: Optional[str] = None,
        icon_url: Optional[str] = None,
    ) -> Server:
        """Create a new server."""
        name = self._validate_server_name(name)

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

        # Add owner as member
        member_id = self._generate_id()
        self._db.execute(
            """INSERT INTO srv_members 
               (id, server_id, user_id, joined_at)
               VALUES (?, ?, ?, ?)""",
            (member_id, server_id, owner_id, now),
        )

        # Assign default role to owner
        mr_id = self._generate_id()
        self._db.execute(
            """INSERT INTO srv_member_roles (id, member_id, role_id, assigned_at)
               VALUES (?, ?, ?, ?)""",
            (mr_id, member_id, role_id, now),
        )

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
            conv = self._messaging.create_server_channel_conversation(
                server_id, channel_id
            ) if hasattr(self._messaging, 'create_server_channel_conversation') else None
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

    def get_server(self, server_id: int, user_id: int) -> Optional[Server]:
        """Get a server by ID if user has access (cached for 5 minutes)."""
        if not self._is_member(server_id, user_id):
            return None

        # Try cache first
        cache_key = f"server:{server_id}"
        if redis_available():
            cached = cache_get(cache_key)
            if cached:
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
            return None

        server = self._row_to_server(row)

        # Cache the server data (5 minute TTL)
        if redis_available():
            cache_set(cache_key, self._server_to_dict(server), ttl=300)

        return server

    def get_servers(self, user_id: int, limit: int = 100) -> List[Server]:
        """Get all servers a user is a member of."""
        limit = min(limit, 200)

        rows = self._db.fetch_all(
            """SELECT s.*,
                      (SELECT COUNT(*) FROM srv_members WHERE server_id = s.id) as member_count,
                      (SELECT COUNT(*) FROM srv_channels WHERE server_id = s.id AND deleted = 0) as channel_count,
                      (SELECT COUNT(*) FROM srv_roles WHERE server_id = s.id AND deleted = 0) as role_count
               FROM srv_servers s
               INNER JOIN srv_members m ON s.id = m.server_id
               WHERE m.user_id = ? AND s.deleted = 0
               ORDER BY s.name
               LIMIT ?""",
            (user_id, limit),
        )

        return [self._row_to_server(row) for row in rows]

    def update_server(
        self,
        user_id: int,
        server_id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        icon_url: Optional[str] = None,
        default_channel_id: Optional[int] = None,
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
                    (default_channel_id, server_id)
                )
                if not channel:
                    raise ChannelNotFoundError("Default channel not found in this server")
            updates.append("default_channel_id = ?")
            params.append(default_channel_id if default_channel_id != 0 else None)
            changes["default_channel_id"] = {"old": server.default_channel_id, "new": default_channel_id}

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

    def delete_server(self, user_id: int, server_id: int) -> bool:
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
        self, user_id: int, server_id: int, new_owner_id: int
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
        user_id: int,
        server_id: int,
        name: str,
        channel_type: ChannelType = ChannelType.TEXT,
        category_id: Optional[int] = None,
        topic: Optional[str] = None,
        nsfw: bool = False,
        slowmode_seconds: int = 0,
    ) -> Channel:
        """Create a new channel in a server."""
        server = self.get_server(server_id, user_id)
        if not server:
            raise ServerNotFoundError("Server not found")

        self.require_permission(user_id, server_id, "channels.manage")

        name = self._validate_channel_name(name)

        if category_id:
            cat = self._db.fetch_one(
                "SELECT 1 FROM srv_categories WHERE id = ? AND server_id = ?",
                (category_id, server_id),
            )
            if not cat:
                raise CategoryNotFoundError("Category not found")

        # Get next position
        pos_row = self._db.fetch_one(
            "SELECT COALESCE(MAX(position), -1) + 1 as next_pos FROM srv_channels WHERE server_id = ?",
            (server_id,),
        )
        position = pos_row["next_pos"] if pos_row else 0

        now = self._get_timestamp()
        channel_id = self._generate_id()

        self._db.execute(
            """INSERT INTO srv_channels 
               (id, server_id, name, channel_type, category_id, position, topic, nsfw, slowmode_seconds, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                channel_id,
                server_id,
                name,
                channel_type.value,
                category_id,
                position,
                topic,
                1 if nsfw else 0,
                slowmode_seconds,
                now,
                now,
            ),
        )

        # Create conversation for text channels if messaging module available
        if channel_type == ChannelType.TEXT and self._messaging:
            conv = self._messaging.create_server_channel_conversation(
                server_id, channel_id
            ) if hasattr(self._messaging, 'create_server_channel_conversation') else None
            if conv:
                self._db.execute(
                    "UPDATE srv_channels SET conversation_id = ? WHERE id = ?",
                    (conv.id, channel_id),
                )

        self._log_audit(
            server_id,
            user_id,
            AuditLogAction.CHANNEL_CREATE,
            "channel",
            channel_id,
        )

        logger.debug(f"Created channel {channel_id} in server {server_id}")

        result = self.get_channel(channel_id, user_id)
        assert result is not None  # Should exist since we just created it
        return result

    def create_category(
        self,
        user_id: int,
        server_id: int,
        name: str,
    ) -> ChannelCategory:
        """Create a new channel category."""
        server = self.get_server(server_id, user_id)
        if not server:
            raise ServerNotFoundError("Server not found")

        self.require_permission(user_id, server_id, "channels.manage")

        name = name.strip()
        if not name:
            raise InvalidChannelNameError("Category name cannot be empty")

        pos_row = self._db.fetch_one(
            "SELECT COALESCE(MAX(position), -1) + 1 as next_pos FROM srv_categories WHERE server_id = ?",
            (server_id,),
        )
        position = pos_row["next_pos"] if pos_row else 0

        now = self._get_timestamp()
        cat_id = self._generate_id()

        self._db.execute(
            """INSERT INTO srv_categories (id, server_id, name, position, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (cat_id, server_id, name, position, now, now),
        )

        row = self._db.fetch_one("SELECT * FROM srv_categories WHERE id = ?", (cat_id,))
        return self._row_to_category(row)

    def get_channel(self, channel_id: int, user_id: int) -> Optional[Channel]:
        """Get a channel by ID if user has access (cached)."""
        # Check channel cache first (channel data rarely changes)
        cache_key = channel_id
        cached_row = self._cache_get(self._channel_cache, cache_key)

        if cached_row is None:
            row = self._db.fetch_one(
                "SELECT * FROM srv_channels WHERE id = ? AND deleted = 0",
                (channel_id,),
            )
            if not row:
                return None
            # Cache the raw row data
            self._cache_set(self._channel_cache, cache_key, dict(row))
            cached_row = dict(row)

        server_id = cached_row["server_id"]

        # Membership check is already cached in _is_member
        if not self._is_member(server_id, user_id):
            return None

        # Check if user is server owner (use cached permission check)
        # has_permission already handles owner check internally
        if not self.has_permission(user_id, server_id, "channels.view", channel_id):
            return None

        return self._row_to_channel(cached_row)

    def get_channels(
        self,
        user_id: int,
        server_id: int,
        channel_type: Optional[ChannelType] = None,
    ) -> List[Channel]:
        """Get all channels in a server."""
        if not self._is_member(server_id, user_id):
            raise ServerAccessDeniedError("Not a member of this server")

        query = "SELECT * FROM srv_channels WHERE server_id = ? AND deleted = 0"
        params: list[int | str] = [server_id]

        if channel_type:
            query += " AND channel_type = ?"
            params.append(channel_type.value)

        query += " ORDER BY position"

        rows = self._db.fetch_all(query, tuple(params))

        channels = []
        for row in rows:
            if self.has_permission(user_id, server_id, "channels.view", row["id"]):
                channels.append(self._row_to_channel(row))

        return channels

    def update_channel(
        self,
        user_id: int,
        channel_id: int,
        name: Optional[str] = None,
        topic: Optional[str] = None,
        nsfw: Optional[bool] = None,
        slowmode_seconds: Optional[int] = None,
        category_id: Optional[int] = None,
    ) -> Channel:
        """Update channel settings."""
        channel = self.get_channel(channel_id, user_id)
        if not channel:
            raise ChannelNotFoundError("Channel not found")

        self.require_permission(user_id, channel.server_id, "channels.manage")

        updates = []
        params = []
        changes = {}

        if name is not None:
            name = self._validate_channel_name(name)
            updates.append("name = ?")
            params.append(name)
            changes["name"] = {"old": channel.name, "new": name}

        if topic is not None:
            updates.append("topic = ?")
            params.append(topic)
            changes["topic"] = {"old": channel.topic, "new": topic}

        if nsfw is not None:
            updates.append("nsfw = ?")
            params.append(1 if nsfw else 0)
            changes["nsfw"] = {"old": channel.nsfw, "new": nsfw}

        if slowmode_seconds is not None:
            updates.append("slowmode_seconds = ?")
            params.append(slowmode_seconds)
            changes["slowmode_seconds"] = {
                "old": channel.slowmode_seconds,
                "new": slowmode_seconds,
            }

        if category_id is not None:
            if category_id != 0:
                cat = self._db.fetch_one(
                    "SELECT 1 FROM srv_categories WHERE id = ? AND server_id = ?",
                    (category_id, channel.server_id),
                )
                if not cat:
                    raise CategoryNotFoundError("Category not found")
            updates.append("category_id = ?")
            params.append(category_id if category_id != 0 else None)
            changes["category_id"] = {"old": channel.category_id, "new": category_id}

        if updates:
            updates.append("updated_at = ?")
            params.append(self._get_timestamp())
            params.append(channel_id)

            self._db.execute(
                f"UPDATE srv_channels SET {', '.join(updates)} WHERE id = ?",
                tuple(params),
            )

            self._log_audit(
                channel.server_id,
                user_id,
                AuditLogAction.CHANNEL_UPDATE,
                "channel",
                channel_id,
                changes,
            )

        result = self.get_channel(channel_id, user_id)
        assert result is not None  # Should exist since we just updated it
        return result

    def delete_channel(self, user_id: int, channel_id: int) -> bool:
        """Delete a channel."""
        channel = self.get_channel(channel_id, user_id)
        if not channel:
            raise ChannelNotFoundError("Channel not found")

        self.require_permission(user_id, channel.server_id, "channels.manage")

        now = self._get_timestamp()
        self._db.execute(
            "UPDATE srv_channels SET deleted = 1, deleted_at = ? WHERE id = ?",
            (now, channel_id),
        )

        self._log_audit(
            channel.server_id,
            user_id,
            AuditLogAction.CHANNEL_DELETE,
            "channel",
            channel_id,
        )

        return True

    def move_channel(self, user_id: int, channel_id: int, position: int) -> Channel:
        """Move a channel to a new position."""
        channel = self.get_channel(channel_id, user_id)
        if not channel:
            raise ChannelNotFoundError("Channel not found")

        self.require_permission(user_id, channel.server_id, "channels.manage")

        self._db.execute(
            "UPDATE srv_channels SET position = ?, updated_at = ? WHERE id = ?",
            (position, self._get_timestamp(), channel_id),
        )

        result = self.get_channel(channel_id, user_id)
        assert result is not None  # Should exist since we just updated it
        return result

    # === Role Operations ===

    def create_role(
        self,
        user_id: int,
        server_id: int,
        name: str,
        permissions: Optional[Dict[str, bool]] = None,
        color: Optional[str] = None,
        hoist: bool = False,
        mentionable: bool = False,
    ) -> Role:
        """Create a new role in a server."""
        server = self.get_server(server_id, user_id)
        if not server:
            raise ServerNotFoundError("Server not found")

        self.require_permission(user_id, server_id, "roles.manage")

        name = self._validate_role_name(name)

        # Get next position (above @everyone which is 0)
        pos_row = self._db.fetch_one(
            "SELECT COALESCE(MAX(position), 0) + 1 as next_pos FROM srv_roles WHERE server_id = ? AND deleted = 0",
            (server_id,),
        )
        position = pos_row["next_pos"] if pos_row else 1

        now = self._get_timestamp()
        role_id = self._generate_id()

        self._db.execute(
            """INSERT INTO srv_roles 
               (id, server_id, name, permissions, color, hoist, mentionable, position, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                role_id,
                server_id,
                name,
                json.dumps(permissions or {}),
                color,
                1 if hoist else 0,
                1 if mentionable else 0,
                position,
                now,
                now,
            ),
        )

        self._log_audit(
            server_id, user_id, AuditLogAction.ROLE_CREATE, "role", role_id
        )

        logger.debug(f"Created role {role_id} in server {server_id}")

        result = self.get_role(role_id, user_id)
        assert result is not None  # Should exist since we just created it
        return result

    def get_role(self, role_id: int, user_id: int) -> Optional[Role]:
        """Get a role by ID."""
        row = self._db.fetch_one(
            "SELECT * FROM srv_roles WHERE id = ? AND deleted = 0",
            (role_id,),
        )

        if not row:
            return None

        if not self._is_member(row["server_id"], user_id):
            return None

        return self._row_to_role(row)

    def get_roles(self, user_id: int, server_id: int) -> List[Role]:
        """Get all roles in a server."""
        if not self._is_member(server_id, user_id):
            raise ServerAccessDeniedError("Not a member of this server")

        rows = self._db.fetch_all(
            "SELECT * FROM srv_roles WHERE server_id = ? AND deleted = 0 ORDER BY position DESC",
            (server_id,),
        )

        return [self._row_to_role(row) for row in rows]

    def update_role(
        self,
        user_id: int,
        role_id: int,
        name: Optional[str] = None,
        permissions: Optional[Dict[str, bool]] = None,
        color: Optional[str] = None,
        hoist: Optional[bool] = None,
        mentionable: Optional[bool] = None,
    ) -> Role:
        """Update role settings."""
        role = self.get_role(role_id, user_id)
        if not role:
            raise RoleNotFoundError("Role not found")

        self.require_permission(user_id, role.server_id, "roles.manage")

        # Check hierarchy
        user_roles = self._get_member_role_rows(role.server_id, user_id)
        server = self.get_server(role.server_id, user_id)
        is_owner = server is not None and server.owner_id == user_id

        if not can_manage_role(user_roles, {"position": role.position}, is_owner):
            raise RoleHierarchyError(
                "Cannot modify a role at or above your highest role"
            )

        updates = []
        params = []
        changes = {}

        if name is not None:
            if role.is_default:
                raise DefaultRoleError("Cannot rename the default role")
            name = self._validate_role_name(name)
            updates.append("name = ?")
            params.append(name)
            changes["name"] = {"old": role.name, "new": name}

        if permissions is not None:
            updates.append("permissions = ?")
            params.append(json.dumps(permissions))
            changes["permissions"] = {"old": role.permissions, "new": permissions}

        if color is not None:
            updates.append("color = ?")
            params.append(color)
            changes["color"] = {"old": role.color, "new": color}

        if hoist is not None:
            updates.append("hoist = ?")
            params.append(1 if hoist else 0)
            changes["hoist"] = {"old": role.hoist, "new": hoist}

        if mentionable is not None:
            updates.append("mentionable = ?")
            params.append(1 if mentionable else 0)
            changes["mentionable"] = {"old": role.mentionable, "new": mentionable}

        if updates:
            updates.append("updated_at = ?")
            params.append(self._get_timestamp())
            params.append(role_id)

            self._db.execute(
                f"UPDATE srv_roles SET {', '.join(updates)} WHERE id = ?",
                tuple(params),
            )

            self._log_audit(
                role.server_id,
                user_id,
                AuditLogAction.ROLE_UPDATE,
                "role",
                role_id,
                changes,
            )

        updated_role = self.get_role(role_id, user_id)
        assert updated_role is not None
        return updated_role

    def delete_role(self, user_id: int, role_id: int) -> bool:
        """Delete a role."""
        role = self.get_role(role_id, user_id)
        if not role:
            raise RoleNotFoundError("Role not found")

        if role.is_default:
            raise DefaultRoleError("Cannot delete the default role")

        self.require_permission(user_id, role.server_id, "roles.manage")

        # Check hierarchy
        user_roles = self._get_member_role_rows(role.server_id, user_id)
        server = self.get_server(role.server_id, user_id)
        is_owner = server is not None and server.owner_id == user_id

        if not can_manage_role(user_roles, {"position": role.position}, is_owner):
            raise RoleHierarchyError(
                "Cannot delete a role at or above your highest role"
            )

        self._db.execute(
            "UPDATE srv_roles SET deleted = 1 WHERE id = ?",
            (role_id,),
        )

        # Remove role from all members
        self._db.execute(
            "DELETE FROM srv_member_roles WHERE role_id = ?",
            (role_id,),
        )

        self._log_audit(
            role.server_id, user_id, AuditLogAction.ROLE_DELETE, "role", role_id
        )

        return True

    def move_role(self, user_id: int, role_id: int, position: int) -> Role:
        """Move a role to a new position in hierarchy."""
        role = self.get_role(role_id, user_id)
        if not role:
            raise RoleNotFoundError("Role not found")

        if role.is_default:
            raise DefaultRoleError("Cannot move the default role")

        self.require_permission(user_id, role.server_id, "roles.manage")

        # Check hierarchy
        user_roles = self._get_member_role_rows(role.server_id, user_id)
        server = self.get_server(role.server_id, user_id)
        is_owner = server is not None and server.owner_id == user_id

        if not can_manage_role(user_roles, {"position": role.position}, is_owner):
            raise RoleHierarchyError(
                "Cannot move a role at or above your highest role"
            )

        # Cannot move above own highest role
        user_highest = max((r.get("position", 0) for r in user_roles), default=0)
        if position >= user_highest and not is_owner:
            raise RoleHierarchyError("Cannot move role above your highest role")

        self._db.execute(
            "UPDATE srv_roles SET position = ?, updated_at = ? WHERE id = ?",
            (position, self._get_timestamp(), role_id),
        )

        result = self.get_role(role_id, user_id)
        assert result is not None  # Should exist since we just updated it
        return result


    # === Member Operations ===

    def add_member(
        self, server_id: int, user_id: int, inviter_id: Optional[int] = None
    ) -> Member:
        """Add a user as a member of a server."""
        # Check if banned
        ban = self._db.fetch_one(
            "SELECT 1 FROM srv_bans WHERE server_id = ? AND user_id = ?",
            (server_id, user_id),
        )
        if ban:
            raise UserBannedError("User is banned from this server")

        # Check if already member
        if self._is_member(server_id, user_id):
            raise MemberExistsError("User is already a member of this server")

        # Verify server exists
        server = self._db.fetch_one(
            "SELECT * FROM srv_servers WHERE id = ? AND deleted = 0",
            (server_id,),
        )
        if not server:
            raise ServerNotFoundError("Server not found")

        now = self._get_timestamp()
        member_id = self._generate_id()

        self._db.execute(
            """INSERT INTO srv_members 
               (id, server_id, user_id, joined_at, inviter_id)
               VALUES (?, ?, ?, ?, ?)""",
            (member_id, server_id, user_id, now, inviter_id),
        )

        # Assign default role
        default_role = self._db.fetch_one(
            "SELECT id FROM srv_roles WHERE server_id = ? AND is_default = 1",
            (server_id,),
        )
        if default_role:
            mr_id = self._generate_id()
            self._db.execute(
                """INSERT INTO srv_member_roles (id, member_id, role_id, assigned_at)
                   VALUES (?, ?, ?, ?)""",
                (mr_id, member_id, default_role["id"], now),
            )

        self._log_audit(
            server_id, user_id, AuditLogAction.MEMBER_JOIN, "member", user_id
        )

        logger.debug(f"Added member {user_id} to server {server_id}")

        result = self.get_member(server_id, user_id)
        assert result is not None  # Should exist since we just created it
        return result

    def get_member(self, server_id: int, user_id: int) -> Optional[Member]:
        """Get a member by user ID."""
        row = self._db.fetch_one(
            "SELECT * FROM srv_members WHERE server_id = ? AND user_id = ?",
            (server_id, user_id),
        )

        if not row:
            return None

        return self._row_to_member(row)

    def get_members(
        self,
        user_id: int,
        server_id: int,
        limit: int = 100,
        after_id: Optional[int] = None,
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

        return [self._row_to_member(row) for row in rows]

    def get_member_user_ids(
        self,
        server_id: int,
        exclude_user_id: Optional[int] = None,
    ) -> List[int]:
        """
        Get just the user IDs of server members (optimized for typing/presence dispatch).
        
        This is faster than get_members() as it only fetches user_id column.
        """
        query = "SELECT user_id FROM srv_members WHERE server_id = ?"
        params = [server_id]

        if exclude_user_id:
            query += " AND user_id != ?"
            params.append(exclude_user_id)

        rows = self._db.fetch_all(query, tuple(params))
        return [row["user_id"] for row in rows]

    def update_member(
        self,
        user_id: int,
        server_id: int,
        member_user_id: int,
        nickname: Optional[str] = None,
        muted: Optional[bool] = None,
        deafened: Optional[bool] = None,
    ) -> Member:
        """Update member settings."""
        if not self._is_member(server_id, user_id):
            raise ServerAccessDeniedError("Not a member of this server")

        member = self.get_member(server_id, member_user_id)
        if not member:
            raise MemberNotFoundError("Member not found")

        # Check permissions for modifying others
        if user_id != member_user_id:
            if nickname is not None:
                self.require_permission(user_id, server_id, "members.manage_nicknames")

        updates = []
        params = []
        changes = {}

        if nickname is not None:
            updates.append("nickname = ?")
            params.append(nickname if nickname else None)
            changes["nickname"] = {"old": member.nickname, "new": nickname}

        if muted is not None:
            self.require_permission(user_id, server_id, "voice.mute_members")
            updates.append("muted = ?")
            params.append(1 if muted else 0)
            changes["muted"] = {"old": member.muted, "new": muted}

        if deafened is not None:
            self.require_permission(user_id, server_id, "voice.deafen_members")
            updates.append("deafened = ?")
            params.append(1 if deafened else 0)
            changes["deafened"] = {"old": member.deafened, "new": deafened}

        if updates:
            params.extend([server_id, member_user_id])

            self._db.execute(
                f"UPDATE srv_members SET {', '.join(updates)} WHERE server_id = ? AND user_id = ?",
                tuple(params),
            )

            if user_id != member_user_id:
                self._log_audit(
                    server_id,
                    user_id,
                    AuditLogAction.MEMBER_UPDATE,
                    "member",
                    member_user_id,
                    changes,
                )

        result = self.get_member(server_id, member_user_id)
        assert result is not None  # Should exist since we just updated it
        return result

    def remove_member(self, user_id: int, server_id: int) -> bool:
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

        self._log_audit(
            server_id, user_id, AuditLogAction.MEMBER_LEAVE, "member", user_id
        )

        return True

    def kick_member(
        self,
        user_id: int,
        server_id: int,
        member_user_id: int,
        reason: Optional[str] = None,
    ) -> bool:
        """Kick a member from a server."""
        server = self.get_server(server_id, user_id)
        if not server:
            raise ServerNotFoundError("Server not found")

        self.require_permission(user_id, server_id, "members.kick")

        if server.owner_id == member_user_id:
            raise CannotModifyOwnerError("Cannot kick the server owner")

        member = self.get_member(server_id, member_user_id)
        if not member:
            raise MemberNotFoundError("Member not found")

        # Check hierarchy
        user_roles = self._get_member_role_rows(server_id, user_id)
        target_roles = self._get_member_role_rows(server_id, member_user_id)
        is_owner = server.owner_id == user_id

        if not can_manage_member(user_roles, target_roles, is_owner, False):
            raise RoleHierarchyError("Cannot kick a member with equal or higher role")

        # Remove member roles
        self._db.execute(
            "DELETE FROM srv_member_roles WHERE member_id = ?",
            (member.id,),
        )

        # Remove member
        self._db.execute(
            "DELETE FROM srv_members WHERE server_id = ? AND user_id = ?",
            (server_id, member_user_id),
        )

        # Invalidate caches for the kicked member
        self._cache_invalidate(self._member_cache, (server_id, member_user_id))
        self._cache_invalidate(self._permission_cache, (member_user_id, server_id, None))
        # Also invalidate any channel-specific permission caches
        for key in list(self._permission_cache.keys()):
            if key[0] == member_user_id and key[1] == server_id:
                self._cache_invalidate(self._permission_cache, key)

        self._log_audit(
            server_id,
            user_id,
            AuditLogAction.MEMBER_KICK,
            "member",
            member_user_id,
            reason=reason,
        )

        return True

    def ban_member(
        self,
        user_id: int,
        server_id: int,
        member_user_id: int,
        reason: Optional[str] = None,
        delete_message_days: int = 0,
    ) -> Ban:
        """Ban a user from a server."""
        server = self.get_server(server_id, user_id)
        if not server:
            raise ServerNotFoundError("Server not found")

        self.require_permission(user_id, server_id, "members.ban")

        if server.owner_id == member_user_id:
            raise CannotModifyOwnerError("Cannot ban the server owner")

        # Check if already banned
        existing = self._db.fetch_one(
            "SELECT 1 FROM srv_bans WHERE server_id = ? AND user_id = ?",
            (server_id, member_user_id),
        )
        if existing:
            raise BanExistsError("User is already banned")

        # Check hierarchy if member
        member = self.get_member(server_id, member_user_id)
        if member:
            user_roles = self._get_member_role_rows(server_id, user_id)
            target_roles = self._get_member_role_rows(server_id, member_user_id)
            is_owner = server.owner_id == user_id

            if not can_manage_member(user_roles, target_roles, is_owner, False):
                raise RoleHierarchyError(
                    "Cannot ban a member with equal or higher role"
                )

            # Remove member
            self._db.execute(
                "DELETE FROM srv_member_roles WHERE member_id = ?",
                (member.id,),
            )
            self._db.execute(
                "DELETE FROM srv_members WHERE server_id = ? AND user_id = ?",
                (server_id, member_user_id),
            )

            # Invalidate caches for the banned member
            self._cache_invalidate(self._member_cache, (server_id, member_user_id))
            for key in list(self._permission_cache.keys()):
                if key[0] == member_user_id and key[1] == server_id:
                    self._cache_invalidate(self._permission_cache, key)

        now = self._get_timestamp()
        ban_id = self._generate_id()

        self._db.execute(
            """INSERT INTO srv_bans (id, server_id, user_id, banned_by, reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (ban_id, server_id, member_user_id, user_id, reason, now),
        )

        self._log_audit(
            server_id,
            user_id,
            AuditLogAction.MEMBER_BAN,
            "member",
            member_user_id,
            reason=reason,
        )

        return Ban(
            id=ban_id,
            server_id=server_id,
            user_id=member_user_id,
            banned_by=user_id,
            reason=reason,
            created_at=now,
        )

    def unban_member(self, user_id: int, server_id: int, banned_user_id: int) -> bool:
        """Unban a user from a server."""
        server = self.get_server(server_id, user_id)
        if not server:
            raise ServerNotFoundError("Server not found")

        self.require_permission(user_id, server_id, "members.ban")

        self._db.execute(
            "DELETE FROM srv_bans WHERE server_id = ? AND user_id = ?",
            (server_id, banned_user_id),
        )

        self._log_audit(
            server_id,
            user_id,
            AuditLogAction.MEMBER_UNBAN,
            "member",
            banned_user_id,
        )

        return True

    def get_bans(self, user_id: int, server_id: int) -> List[Ban]:
        """Get all bans for a server."""
        server = self.get_server(server_id, user_id)
        if not server:
            raise ServerNotFoundError("Server not found")

        self.require_permission(user_id, server_id, "members.ban")

        rows = self._db.fetch_all(
            "SELECT * FROM srv_bans WHERE server_id = ? ORDER BY created_at DESC",
            (server_id,),
        )

        return [self._row_to_ban(row) for row in rows]

    # === Role Assignment ===

    def assign_role(
        self, user_id: int, server_id: int, member_user_id: int, role_id: int
    ) -> bool:
        """Assign a role to a member."""
        server = self.get_server(server_id, user_id)
        if not server:
            raise ServerNotFoundError("Server not found")

        self.require_permission(user_id, server_id, "members.manage_roles")

        role = self.get_role(role_id, user_id)
        if not role or role.server_id != server_id:
            raise RoleNotFoundError("Role not found")

        member = self.get_member(server_id, member_user_id)
        if not member:
            raise MemberNotFoundError("Member not found")

        # Check hierarchy
        user_roles = self._get_member_role_rows(server_id, user_id)
        is_owner = server.owner_id == user_id

        if not can_manage_role(user_roles, {"position": role.position}, is_owner):
            raise RoleHierarchyError("Cannot assign a role at or above your highest role")

        # Check if already has role
        existing = self._db.fetch_one(
            "SELECT 1 FROM srv_member_roles WHERE member_id = ? AND role_id = ?",
            (member.id, role_id),
        )
        if existing:
            return True

        now = self._get_timestamp()
        mr_id = self._generate_id()

        self._db.execute(
            """INSERT INTO srv_member_roles (id, member_id, role_id, assigned_at, assigned_by)
               VALUES (?, ?, ?, ?, ?)""",
            (mr_id, member.id, role_id, now, user_id),
        )

        # Invalidate permission cache for the affected member
        for key in list(self._permission_cache.keys()):
            if key[0] == member_user_id and key[1] == server_id:
                self._cache_invalidate(self._permission_cache, key)

        self._log_audit(
            server_id,
            user_id,
            AuditLogAction.MEMBER_ROLE_ADD,
            "member",
            member_user_id,
            {"role_id": role_id},
        )

        return True

    def remove_role(
        self, user_id: int, server_id: int, member_user_id: int, role_id: int
    ) -> bool:
        """Remove a role from a member."""
        server = self.get_server(server_id, user_id)
        if not server:
            raise ServerNotFoundError("Server not found")

        self.require_permission(user_id, server_id, "members.manage_roles")

        role = self.get_role(role_id, user_id)
        if not role or role.server_id != server_id:
            raise RoleNotFoundError("Role not found")

        if role.is_default:
            raise DefaultRoleError("Cannot remove the default role")

        member = self.get_member(server_id, member_user_id)
        if not member:
            raise MemberNotFoundError("Member not found")

        # Check hierarchy
        user_roles = self._get_member_role_rows(server_id, user_id)
        is_owner = server.owner_id == user_id

        if not can_manage_role(user_roles, {"position": role.position}, is_owner):
            raise RoleHierarchyError(
                "Cannot remove a role at or above your highest role"
            )

        self._db.execute(
            "DELETE FROM srv_member_roles WHERE member_id = ? AND role_id = ?",
            (member.id, role_id),
        )

        # Invalidate permission cache for the affected member
        for key in list(self._permission_cache.keys()):
            if key[0] == member_user_id and key[1] == server_id:
                self._cache_invalidate(self._permission_cache, key)

        self._log_audit(
            server_id,
            user_id,
            AuditLogAction.MEMBER_ROLE_REMOVE,
            "member",
            member_user_id,
            {"role_id": role_id},
        )

        return True

    def get_member_roles(self, server_id: int, member_user_id: int) -> List[Role]:
        """Get all roles assigned to a member."""
        member = self.get_member(server_id, member_user_id)
        if not member:
            return []

        rows = self._db.fetch_all(
            """SELECT r.* FROM srv_roles r
               INNER JOIN srv_member_roles mr ON r.id = mr.role_id
               WHERE mr.member_id = ? AND r.deleted = 0
               ORDER BY r.position DESC""",
            (member.id,),
        )

        return [self._row_to_role(row) for row in rows]


    # === Permission Operations ===

    def get_channel_override(
        self,
        channel_id: int,
        target_type: str,
        target_id: int,
    ) -> Optional[ChannelOverride]:
        """Get permission override for a channel."""
        row = self._db.fetch_one(
            """SELECT * FROM srv_channel_overrides 
               WHERE channel_id = ? AND target_type = ? AND target_id = ?""",
            (channel_id, target_type, target_id),
        )

        if not row:
            return None

        return self._row_to_override(row)

    def set_channel_override(
        self,
        user_id: int,
        channel_id: int,
        target_type: str,
        target_id: int,
        allow: Optional[Dict[str, bool]] = None,
        deny: Optional[Dict[str, bool]] = None,
    ) -> ChannelOverride:
        """Set permission override for a channel."""
        channel = self.get_channel(channel_id, user_id)
        if not channel:
            raise ChannelNotFoundError("Channel not found")

        self.require_permission(user_id, channel.server_id, "channels.manage")

        now = self._get_timestamp()

        existing = self._db.fetch_one(
            """SELECT id FROM srv_channel_overrides 
               WHERE channel_id = ? AND target_type = ? AND target_id = ?""",
            (channel_id, target_type, target_id),
        )

        if existing:
            self._db.execute(
                """UPDATE srv_channel_overrides 
                   SET allow = ?, deny = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    json.dumps(allow or {}),
                    json.dumps(deny or {}),
                    now,
                    existing["id"],
                ),
            )
            override_id = existing["id"]
            action = AuditLogAction.OVERRIDE_UPDATE
        else:
            override_id = self._generate_id()
            self._db.execute(
                """INSERT INTO srv_channel_overrides 
                   (id, channel_id, target_type, target_id, allow, deny, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    override_id,
                    channel_id,
                    target_type,
                    target_id,
                    json.dumps(allow or {}),
                    json.dumps(deny or {}),
                    now,
                    now,
                ),
            )
            action = AuditLogAction.OVERRIDE_CREATE

        self._log_audit(
            channel.server_id,
            user_id,
            action,
            "override",
            override_id,
            {"target_type": target_type, "target_id": target_id},
        )

        result = self.get_channel_override(channel_id, target_type, target_id)
        assert result is not None  # Should exist since we just created/updated it
        return result

    def delete_channel_override(
        self,
        user_id: int,
        channel_id: int,
        target_type: str,
        target_id: int,
    ) -> bool:
        """Delete a permission override."""
        channel = self.get_channel(channel_id, user_id)
        if not channel:
            raise ChannelNotFoundError("Channel not found")

        self.require_permission(user_id, channel.server_id, "channels.manage")

        self._db.execute(
            """DELETE FROM srv_channel_overrides 
               WHERE channel_id = ? AND target_type = ? AND target_id = ?""",
            (channel_id, target_type, target_id),
        )

        self._log_audit(
            channel.server_id,
            user_id,
            AuditLogAction.OVERRIDE_DELETE,
            "override",
            None,
            {"channel_id": channel_id, "target_type": target_type, "target_id": target_id},
        )

        return True

    def has_permission(
        self,
        user_id: int,
        server_id: int,
        permission: str,
        channel_id: Optional[int] = None,
    ) -> bool:
        """Check if a user has a permission in a server/channel."""
        permissions = self.get_permissions(user_id, server_id, channel_id)
        return check_permission(permissions, permission)

    def get_permissions(
        self,
        user_id: int,
        server_id: int,
        channel_id: Optional[int] = None,
    ) -> Dict[str, bool]:
        """Get all permissions for a user in a server/channel (cached)."""
        cache_key = (user_id, server_id, channel_id)
        cached = self._cache_get(self._permission_cache, cache_key)
        if cached is not None:
            return cached

        # Check server owner cache first
        owner_id = self._cache_get(self._server_owner_cache, server_id)
        if owner_id is None:
            server = self._db.fetch_one(
                "SELECT owner_id FROM srv_servers WHERE id = ? AND deleted = 0",
                (server_id,),
            )
            if not server:
                return {}
            owner_id = server["owner_id"]
            self._cache_set(self._server_owner_cache, server_id, owner_id)

        is_owner = owner_id == user_id

        # Get member's roles (cached)
        roles_cache_key = (server_id, user_id)
        role_rows = self._cache_get(self._member_roles_cache, roles_cache_key)
        if role_rows is None:
            role_rows = self._get_member_role_rows(server_id, user_id)
            self._cache_set(self._member_roles_cache, roles_cache_key, role_rows)

        # Calculate base permissions
        base_perms = calculate_base_permissions(role_rows, is_owner)

        if not channel_id:
            self._cache_set(self._permission_cache, cache_key, base_perms)
            return base_perms

        # Get channel overrides - use cached member if available
        member_cache_key = (server_id, user_id)
        member = self._cache_get(self._member_cache, member_cache_key)
        if member is None or member is False:
            member = self.get_member(server_id, user_id)
            if member:
                self._cache_set(self._member_cache, member_cache_key, member)

        if not member or member is False:
            return {}

        # Get role overrides for this channel
        role_ids = [r["id"] for r in role_rows]
        role_overrides = []

        if role_ids:
            placeholders = ",".join("?" * len(role_ids))
            override_rows = self._db.fetch_all(
                f"""SELECT * FROM srv_channel_overrides 
                   WHERE channel_id = ? AND target_type = 'role' AND target_id IN ({placeholders})""",
                (channel_id, *role_ids),
            )
            role_overrides = [dict(row) for row in override_rows]

        # Get member override
        member_override_row = self._db.fetch_one(
            """SELECT * FROM srv_channel_overrides 
               WHERE channel_id = ? AND target_type = 'member' AND target_id = ?""",
            (channel_id, user_id),
        )
        member_override = dict(member_override_row) if member_override_row else None

        result = apply_channel_overrides(base_perms, role_overrides, member_override)
        self._cache_set(self._permission_cache, cache_key, result)
        return result

    def require_permission(
        self,
        user_id: int,
        server_id: int,
        permission: str,
        channel_id: Optional[int] = None,
    ) -> None:
        """Require a permission, raising if not granted."""
        if not self.has_permission(user_id, server_id, permission, channel_id):
            raise PermissionDeniedError(
                f"Missing required permission: {permission}", permission
            )

    # === Invite Operations ===

    def create_invite(
        self,
        user_id: int,
        channel_id: int,
        max_age: int = 86400,
        max_uses: int = 0,
        temporary: bool = False,
    ) -> Invite:
        """Create an invite to a channel."""
        channel = self.get_channel(channel_id, user_id)
        if not channel:
            raise ChannelNotFoundError("Channel not found")

        self.require_permission(user_id, channel.server_id, "invites.create")

        now = self._get_timestamp()
        expires_at = now + (max_age * 1000) if max_age > 0 else None

        invite_id = self._generate_id()
        code = self._generate_invite_code()

        self._db.execute(
            """INSERT INTO srv_invites 
               (id, code, server_id, channel_id, inviter_id, max_age, max_uses, temporary, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                invite_id,
                code,
                channel.server_id,
                channel_id,
                user_id,
                max_age,
                max_uses,
                1 if temporary else 0,
                now,
                expires_at,
            ),
        )

        self._log_audit(
            channel.server_id,
            user_id,
            AuditLogAction.INVITE_CREATE,
            "invite",
            invite_id,
        )

        result = self.get_invite(code)
        assert result is not None  # Should exist since we just created it
        return result

    def get_invite(self, code: str) -> Optional[Invite]:
        """Get an invite by code."""
        row = self._db.fetch_one(
            "SELECT * FROM srv_invites WHERE code = ? AND revoked = 0",
            (code,),
        )

        if not row:
            return None

        return self._row_to_invite(row)

    def get_invites(self, user_id: int, server_id: int) -> List[Invite]:
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

    def use_invite(self, user_id: int, code: str) -> Member:
        """Use an invite to join a server."""
        invite = self.get_invite(code)
        if not invite:
            raise InviteNotFoundError("Invite not found or has been revoked")

        now = self._get_timestamp()

        # Check expiration
        if invite.expires_at and invite.expires_at < now:
            raise InviteExpiredError("Invite has expired", invite.expires_at)

        # Check max uses
        if invite.max_uses > 0 and invite.uses >= invite.max_uses:
            raise InviteMaxUsesError(
                "Invite has reached maximum uses", invite.max_uses, invite.uses
            )

        # Add member
        member = self.add_member(invite.server_id, user_id, invite.inviter_id)

        # Increment uses
        self._db.execute(
            "UPDATE srv_invites SET uses = uses + 1 WHERE code = ?",
            (code,),
        )

        return member

    def delete_invite(self, user_id: int, code: str) -> bool:
        """Delete an invite."""
        invite = self.get_invite(code)
        if not invite:
            raise InviteNotFoundError("Invite not found")

        server = self.get_server(invite.server_id, user_id)
        if not server:
            raise ServerNotFoundError("Server not found")

        # Can delete own invites or with manage permission
        if invite.inviter_id != user_id:
            self.require_permission(user_id, invite.server_id, "invites.manage")

        self._db.execute(
            "UPDATE srv_invites SET revoked = 1 WHERE code = ?",
            (code,),
        )

        self._log_audit(
            invite.server_id,
            user_id,
            AuditLogAction.INVITE_DELETE,
            "invite",
            invite.id,
        )

        return True

    # === Channel Messaging ===

    def send_channel_message(
        self,
        user_id: int,
        channel_id: int,
        content: str,
        attachments: Optional[List[Dict[str, Any]]] = None,
        reply_to_id: Optional[int] = None,
    ) -> Any:
        """Send a message to a text channel."""
        channel = self.get_channel(channel_id, user_id)
        if not channel:
            raise ChannelNotFoundError("Channel not found")

        if channel.channel_type != ChannelType.TEXT:
            raise ChannelTypeError("Can only send messages to text channels")

        self.require_permission(user_id, channel.server_id, "messages.send", channel_id)

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

    def get_channel_messages(
        self,
        user_id: int,
        channel_id: int,
        limit: int = 50,
        before_id: Optional[int] = None,
        after_id: Optional[int] = None,
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

        return self._messaging.get_messages(
            user_id=user_id,
            conversation_id=channel.conversation_id,
            limit=limit,
            before_id=before_id,
            after_id=after_id,
        )

    # === Audit Log ===

    def get_audit_log(
        self,
        user_id: int,
        server_id: int,
        limit: int = 50,
        action_type: Optional[AuditLogAction] = None,
        before_id: Optional[int] = None,
    ) -> List[AuditLogEntry]:
        """Get audit log entries for a server."""
        server = self.get_server(server_id, user_id)
        if not server:
            raise ServerNotFoundError("Server not found")

        self.require_permission(user_id, server_id, "server.view_audit_log")

        limit = min(limit, 100)

        query = "SELECT * FROM srv_audit_log WHERE server_id = ?"
        params: list[int | str] = [server_id]

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

    def _is_member(self, server_id: int, user_id: int) -> bool:
        """Check if user is a member of a server (cached)."""
        cache_key = (server_id, user_id)
        cached = self._cache_get(self._member_cache, cache_key)
        if cached is not None:
            return cached is not False  # False means not a member

        row = self._db.fetch_one(
            "SELECT 1 FROM srv_members WHERE server_id = ? AND user_id = ?",
            (server_id, user_id),
        )
        result = row is not None
        self._cache_set(self._member_cache, cache_key, result if result else False)
        return result

    def _get_member_role_rows(self, server_id: int, user_id: int) -> List[Dict[str, Any]]:
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
            icon_url=row["icon_url"],
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
            icon_url=data.get("icon_url"),
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
            slowmode_seconds=row["slowmode_seconds"],
            conversation_id=row["conversation_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            deleted=bool(row["deleted"]),
            deleted_at=row["deleted_at"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else None,
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
            is_default=bool(row["is_default"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            deleted=bool(row["deleted"]),
        )

    def _row_to_member(self, row: Dict[str, Any]) -> Member:
        """Convert database row to Member model."""
        # Get role IDs
        role_rows = self._db.fetch_all(
            "SELECT role_id FROM srv_member_roles WHERE member_id = ?",
            (row["id"],),
        )
        role_ids = [r["role_id"] for r in role_rows]

        return Member(
            id=row["id"],
            server_id=row["server_id"],
            user_id=row["user_id"],
            nickname=row["nickname"],
            joined_at=row["joined_at"],
            muted=bool(row["muted"]),
            deafened=bool(row["deafened"]),
            inviter_id=row["inviter_id"],
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
        changes = row["changes"]
        if isinstance(changes, str):
            changes = json.loads(changes) if changes else None

        return AuditLogEntry(
            id=row["id"],
            server_id=row["server_id"],
            user_id=row["user_id"],
            action=AuditLogAction(row["action"]),
            target_type=row["target_type"],
            target_id=row["target_id"],
            changes=changes,
            reason=row["reason"],
            created_at=row["created_at"],
        )
