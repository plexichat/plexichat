import json
from typing import Optional, List

import utils.logger as logger
from src.core.base import SnowflakeID
from src.core.database import (
    cache_get,
    cache_set,
    cache_delete,
    invalidate_pattern,
    valkey_available,
)

from ..models import (
    Server,
    ChannelType,
    AuditLogAction,
    DEFAULT_EVERYONE_PERMISSIONS,
)
from ..exceptions import (
    ServerNotFoundError,
    ServerAccessDeniedError,
    ChannelNotFoundError,
    InvalidServerNameError,
    MemberNotFoundError,
    ServerLimitReachedError,
)
from src.core.database.cache import cached
from .converters import _row_to_server, _server_to_dict, _dict_to_server
from .protocol import ServerProtocol


class ServerOpsMixin(ServerProtocol):
    """Mixin for server CRUD operations."""

    def create_server(
        self,
        owner_id: SnowflakeID,
        name: str,
        description: Optional[str] = None,
        icon_url: Optional[str] = None,
        is_public: Optional[bool] = None,
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
                f"Server name too long (max {max_len} characters)", name
            )

        # Enforce the per-user server limit. A finite license "max_servers"
        # value overrides the configured "max_servers_per_user"; free tier is
        # not restricted (preserves prior behaviour).
        try:
            from utils.licensing import (
                get_license_limit,
                is_free_tier,
                is_limit_enforced,
            )

            _server_cap = -1
            if not is_free_tier():
                _server_cap = get_license_limit("max_servers", default=-1)
                if not is_limit_enforced(_server_cap):
                    _server_cap = int(self._config.get("max_servers_per_user", 100))
            if is_limit_enforced(_server_cap):
                _row = self._db.fetch_one(
                    "SELECT COUNT(*) AS c FROM srv_servers WHERE owner_id = ?",
                    (owner_id,),
                )
                _owned = int(_row["c"]) if _row else 0
                if _owned >= _server_cap:
                    raise ServerLimitReachedError(
                        f"Server limit reached: you may create at most "
                        f"{_server_cap} servers.",
                        _server_cap,
                    )
        except (RuntimeError, ImportError):
            pass

        now = self._get_timestamp()
        server_id = self._generate_id()

        description_encrypted = None
        if description:
            if self._encrypt_descriptions:
                from src.utils.encryption import encrypt_data

                description_encrypted = encrypt_data(description)
            else:
                description_encrypted = description

        self._db.execute(
            """INSERT INTO srv_servers
               (id, name, owner_id, description_encrypted, icon_url, is_public,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                server_id,
                name,
                owner_id,
                description_encrypted,
                icon_url,
                1 if is_public else 0,
                now,
                now,
            ),
        )

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

        self._db.execute(
            "UPDATE srv_servers SET default_role_id = ? WHERE id = ?",
            (role_id, server_id),
        )

        logger.debug(
            f"create_server: sid={server_id}, owner={owner_id}, role_id={role_id}"
        )

        member_id = self._generate_id()
        self._db.execute(
            """INSERT INTO srv_members
               (id, server_id, user_id, joined_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (member_id, server_id, owner_id, now, now),
        )

        mr_id = self._generate_id()
        self._db.execute(
            """INSERT INTO srv_member_roles (id, member_id, role_id, assigned_at)
               VALUES (?, ?, ?, ?)""",
            (mr_id, member_id, role_id, now),
        )

        logger.debug(
            f"create_server: sid={server_id}, owner={owner_id}, member_id={member_id}"
        )

        self._cache_invalidate(self._member_cache_prefix, f"{server_id}:{owner_id}")
        self._cache_invalidate(
            self._member_roles_cache_prefix, f"{server_id}:{owner_id}"
        )
        self._cache_invalidate(
            self._permission_cache_prefix, f"{owner_id}:{server_id}:"
        )

        if valkey_available():
            cache_delete(f"user_servers:{owner_id}")
            cache_delete(f"server:{server_id}")
            cache_delete(f"server_channels:{server_id}")

        channel_id = self._generate_id()
        self._db.execute(
            """INSERT INTO srv_channels
               (id, server_id, name, channel_type, position, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (channel_id, server_id, "general", ChannelType.TEXT.value, 0, now, now),
        )

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

        self._db.execute(
            "UPDATE srv_servers SET system_channel_id = ? WHERE id = ?",
            (channel_id, server_id),
        )

        try:
            import src.core.automod as automod

            automod.ensure_default_rules(server_id, owner_id)
        except Exception as e:
            logger.warning(
                f"Failed to initialize default automod rules for server {server_id}: {e}"
            )

        self._log_audit(server_id, owner_id, AuditLogAction.SERVER_CREATE)

        logger.debug(f"Created server {server_id} for owner {owner_id}")

        result = self.get_server(server_id, owner_id)
        assert result is not None
        return result

    def get_server(
        self, server_id: SnowflakeID, user_id: SnowflakeID
    ) -> Optional[Server]:
        """Get a server by ID if user has access (cached for 5 minutes)."""
        logger.debug(f"get_server: sid={server_id}, uid={user_id}")
        is_member = self._is_member(server_id, user_id)
        if not is_member:
            logger.warning(
                f"get_server: user {user_id} is NOT a member of server {server_id}"
            )
            return None

        cache_key = f"server:{server_id}"
        if valkey_available():
            cached = cache_get(cache_key)
            if cached:
                logger.debug(f"get_server: cache hit for {server_id}")
                return _dict_to_server(cached)

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
        server = _row_to_server(row, self._encrypt_descriptions)

        if valkey_available():
            cache_set(cache_key, _server_to_dict(server), ttl=300)

        return server

    @cached(ttl=15, prefix="user_servers")
    def get_servers(self, user_id: SnowflakeID, limit: int = 100) -> List[Server]:
        """Get all servers a user is a member of with optimized count fetching (cached)."""
        limit = min(limit, 200)

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

        return [_row_to_server(row, self._encrypt_descriptions) for row in rows]

    def server_exists(self, server_id: SnowflakeID) -> bool:
        """Check if a server exists by ID (without permission check)."""
        row = self._db.fetch_one(
            "SELECT 1 FROM srv_servers WHERE id = ? AND deleted = 0",
            (server_id,),
        )
        return row is not None

    def update_server(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        name: Optional[str] = None,
        description: Optional[str] = None,
        icon_url: Optional[str] = None,
        default_channel_id: Optional[SnowflakeID] = None,
        system_channel_id: Optional[SnowflakeID] = None,
        verification_level: Optional[int] = None,
        default_message_notifications: Optional[int] = None,
        max_reactions_per_message: Optional[int] = None,
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
            if self._encrypt_descriptions:
                from src.utils.encryption import encrypt_data

                description_encrypted = encrypt_data(description)
            else:
                description_encrypted = description
            updates.append("description_encrypted = ?")
            params.append(description_encrypted)
            changes["description"] = {"old": server.description, "new": description}

        if icon_url is not None:
            updates.append("icon_url = ?")
            params.append(icon_url)
            changes["icon_url"] = {"old": server.icon_url, "new": icon_url}

        if default_channel_id is not None:
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

        if system_channel_id is not None:
            try:
                scid = int(system_channel_id)
            except (ValueError, TypeError):
                raise ValueError("system_channel_id must be a valid channel id")
            if scid == 0:
                # Sentinel 0 clears the system channel.
                updates.append("system_channel_id = ?")
                params.append(None)
                changes["system_channel_id"] = {
                    "old": server.system_channel_id,
                    "new": None,
                }
            else:
                channel = self._db.fetch_one(
                    "SELECT 1 FROM srv_channels WHERE id = ? AND server_id = ? AND deleted = 0",
                    (scid, server_id),
                )
                if not channel:
                    raise ChannelNotFoundError(
                        "System channel not found in this server"
                    )
                updates.append("system_channel_id = ?")
                params.append(scid)
                changes["system_channel_id"] = {
                    "old": server.system_channel_id,
                    "new": scid,
                }

        if verification_level is not None:
            if not 0 <= int(verification_level) <= 4:
                raise ValueError("verification_level must be between 0 and 4")
            updates.append("verification_level = ?")
            params.append(int(verification_level))
            changes["verification_level"] = {
                "old": server.verification_level,
                "new": verification_level,
            }

        if default_message_notifications is not None:
            if not 0 <= int(default_message_notifications) <= 1:
                raise ValueError(
                    "default_message_notifications must be between 0 and 1"
                )
            updates.append("default_message_notifications = ?")
            params.append(int(default_message_notifications))
            changes["default_message_notifications"] = {
                "old": server.default_message_notifications,
                "new": default_message_notifications,
            }

        if max_reactions_per_message is not None:
            if not 1 <= int(max_reactions_per_message) <= 200:
                raise ValueError("max_reactions_per_message must be between 1 and 200")
            updates.append("max_reactions_per_message = ?")
            params.append(int(max_reactions_per_message))
            changes["max_reactions_per_message"] = {
                "old": server.max_reactions_per_message,
                "new": max_reactions_per_message,
            }

        if updates:
            updates.append("updated_at = ?")
            params.append(self._get_timestamp())
            params.append(server_id)

            allowed_columns = {
                "name",
                "description_encrypted",
                "icon",
                "icon_url",
                "banner",
                "owner_id",
                "region",
                "afk_channel_id",
                "afk_timeout",
                "system_channel_id",
                "system_channel_flags",
                "rules_channel_id",
                "public_updates_channel_id",
                "preferred_locale",
                "features",
                "verification_level",
                "default_message_notifications",
                "explicit_content_filter",
                "mfa_level",
                "max_members",
                "max_presences",
                "max_video_channel_users",
                "premium_tier",
                "premium_subscription_count",
                "widget_channel_id",
                "widget_enabled",
                "default_channel_id",
                "max_reactions_per_message",
            }
            for update in updates:
                col_name = update.split(" = ")[0]
                if col_name == "updated_at":
                    continue
                if col_name not in allowed_columns:
                    raise ValueError(f"Invalid column name: {col_name}")

            now = self._get_timestamp()
            for update in updates:
                if "name = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE srv_servers SET name = ?, updated_at = ? WHERE id = ?",
                        (params[idx], now, server_id),
                    )
                elif "description_encrypted = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE srv_servers SET description_encrypted = ?, updated_at = ? WHERE id = ?",
                        (params[idx], now, server_id),
                    )
                elif "icon_url = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE srv_servers SET icon_url = ?, updated_at = ? WHERE id = ?",
                        (params[idx], now, server_id),
                    )
                elif "icon = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE srv_servers SET icon = ?, updated_at = ? WHERE id = ?",
                        (params[idx], now, server_id),
                    )
                elif "banner = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE srv_servers SET banner = ?, updated_at = ? WHERE id = ?",
                        (params[idx], now, server_id),
                    )
                elif "owner_id = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE srv_servers SET owner_id = ?, updated_at = ? WHERE id = ?",
                        (params[idx], now, server_id),
                    )
                elif "region = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE srv_servers SET region = ?, updated_at = ? WHERE id = ?",
                        (params[idx], now, server_id),
                    )
                elif "afk_channel_id = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE srv_servers SET afk_channel_id = ?, updated_at = ? WHERE id = ?",
                        (params[idx], now, server_id),
                    )
                elif "afk_timeout = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE srv_servers SET afk_timeout = ?, updated_at = ? WHERE id = ?",
                        (params[idx], now, server_id),
                    )
                elif "system_channel_id = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE srv_servers SET system_channel_id = ?, updated_at = ? WHERE id = ?",
                        (params[idx], now, server_id),
                    )
                elif "system_channel_flags = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE srv_servers SET system_channel_flags = ?, updated_at = ? WHERE id = ?",
                        (params[idx], now, server_id),
                    )
                elif "rules_channel_id = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE srv_servers SET rules_channel_id = ?, updated_at = ? WHERE id = ?",
                        (params[idx], now, server_id),
                    )
                elif "public_updates_channel_id = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE srv_servers SET public_updates_channel_id = ?, updated_at = ? WHERE id = ?",
                        (params[idx], now, server_id),
                    )
                elif "preferred_locale = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE srv_servers SET preferred_locale = ?, updated_at = ? WHERE id = ?",
                        (params[idx], now, server_id),
                    )
                elif "features = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE srv_servers SET features = ?, updated_at = ? WHERE id = ?",
                        (params[idx], now, server_id),
                    )
                elif "verification_level = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE srv_servers SET verification_level = ?, updated_at = ? WHERE id = ?",
                        (params[idx], now, server_id),
                    )
                elif "default_message_notifications = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE srv_servers SET default_message_notifications = ?, updated_at = ? WHERE id = ?",
                        (params[idx], now, server_id),
                    )
                elif "explicit_content_filter = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE srv_servers SET explicit_content_filter = ?, updated_at = ? WHERE id = ?",
                        (params[idx], now, server_id),
                    )
                elif "mfa_level = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE srv_servers SET mfa_level = ?, updated_at = ? WHERE id = ?",
                        (params[idx], now, server_id),
                    )
                elif "max_members = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE srv_servers SET max_members = ?, updated_at = ? WHERE id = ?",
                        (params[idx], now, server_id),
                    )
                elif "max_presences = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE srv_servers SET max_presences = ?, updated_at = ? WHERE id = ?",
                        (params[idx], now, server_id),
                    )
                elif "max_video_channel_users = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE srv_servers SET max_video_channel_users = ?, updated_at = ? WHERE id = ?",
                        (params[idx], now, server_id),
                    )
                elif "premium_tier = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE srv_servers SET premium_tier = ?, updated_at = ? WHERE id = ?",
                        (params[idx], now, server_id),
                    )
                elif "premium_subscription_count = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE srv_servers SET premium_subscription_count = ?, updated_at = ? WHERE id = ?",
                        (params[idx], now, server_id),
                    )
                elif "widget_channel_id = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE srv_servers SET widget_channel_id = ?, updated_at = ? WHERE id = ?",
                        (params[idx], now, server_id),
                    )
                elif "widget_enabled = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE srv_servers SET widget_enabled = ?, updated_at = ? WHERE id = ?",
                        (params[idx], now, server_id),
                    )
                elif "default_channel_id = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE srv_servers SET default_channel_id = ?, updated_at = ? WHERE id = ?",
                        (params[idx], now, server_id),
                    )
                elif "max_reactions_per_message = ?" in update:
                    idx = updates.index(update)
                    self._db.execute(
                        "UPDATE srv_servers SET max_reactions_per_message = ?, updated_at = ? WHERE id = ?",
                        (params[idx], now, server_id),
                    )

            self._log_audit(
                server_id,
                user_id,
                AuditLogAction.SERVER_UPDATE,
                "server",
                server_id,
                changes,
            )

            if valkey_available():
                cache_delete(f"server:{server_id}")
                # Server rows embed in the per-user server-list tiers
                # (@cached get_servers -> "user_servers:*", and the API-layer
                # GET /servers cache "user_servers_api:*"); drop them too so
                # PATCHes reflect promptly instead of after their TTLs.
                invalidate_pattern("user_servers:*")
                invalidate_pattern("user_servers_api:*")

        result = self.get_server(server_id, user_id)
        assert result is not None
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

        if valkey_available():
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

        # Ownership feeds several caches; clear them all so the transfer takes
        # effect immediately rather than after the relevant TTL. In particular
        # the old owner must not keep ``is_owner=True`` administrator access.
        self._invalidate_owner_cache(server_id)
        # ``server:{id}`` embeds owner_id in the serialized Server row.
        cache_delete(f"server:{server_id}")
        self._invalidate_permission_caches(server_id, user_id)
        self._invalidate_permission_caches(server_id, new_owner_id)

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
