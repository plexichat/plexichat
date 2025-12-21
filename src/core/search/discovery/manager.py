"""
Discovery manager - Handle server discovery operations.
"""

import time
import json
from typing import List, Optional, Dict, Any

import utils.config as config
import utils.logger as logger
from src.utils.encryption import generate_snowflake_id

from ..models import ServerListing, ServerCategory, VerificationLevel
from ..exceptions import (
    ServerNotListedError,
    MinimumMembersError,
    BumpCooldownError,
    SearchPermissionError,
)
from .categories import CategoryManager
from .verification import VerificationManager


class DiscoveryManager:
    """Manage server discovery listings."""

    def __init__(self, db, servers_module=None):
        self._db = db
        self._servers = servers_module
        self._categories = CategoryManager(db)
        self._verification = VerificationManager(db, servers_module)
        self._config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        """Load discovery configuration."""
        defaults = {
            "min_members_for_listing": 10,
            "bump_cooldown_hours": 4,
            "max_tags": 10,
        }

        discovery_config = config.get("search", {}).get("discovery", {})
        return {**defaults, **discovery_config}

    def _get_timestamp(self) -> int:
        """Get current timestamp in milliseconds."""
        return int(time.time() * 1000)

    def _generate_id(self) -> int:
        """Generate a new Snowflake ID."""
        return generate_snowflake_id()

    def list_server(
        self,
        user_id: int,
        server_id: int,
        category: str,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> ServerListing:
        """
        List a server in the public directory.
        
        Args:
            user_id: ID of user listing server
            server_id: ID of server to list
            category: Category for the listing
            description: Optional description override
            tags: Optional tags for discovery
            
        Returns:
            ServerListing object
        """
        self._check_list_permission(user_id, server_id)

        self._categories.validate_category(category)

        member_count = self._get_member_count(server_id)
        min_members = self._config.get("min_members_for_listing", 10)

        if member_count < min_members:
            raise MinimumMembersError(
                f"Server needs at least {min_members} members to be listed",
                required=min_members,
                current=member_count
            )

        existing = self._get_listing(server_id)
        now = self._get_timestamp()

        if tags:
            max_tags = self._config.get("max_tags", 10)
            tags = tags[:max_tags]

        tags_json = json.dumps(tags) if tags else "[]"

        server_info = self._get_server_info(server_id)
        server_name = server_info.get("name", "") if server_info else ""
        server_icon = server_info.get("icon_url") if server_info else None

        if existing:
            self._db.execute(
                """UPDATE search_server_listings 
                   SET category = ?, description = ?, tags = ?, 
                       member_count = ?, listed_by = ?
                   WHERE server_id = ?""",
                (category, description, tags_json, member_count, user_id, server_id)
            )
            listing_id = existing["id"]
        else:
            listing_id = self._generate_id()
            self._db.execute(
                """INSERT INTO search_server_listings 
                   (id, server_id, category, description, tags, member_count,
                    online_count, verification_level, is_verified, is_partnered,
                    listed_at, bumped_at, bump_count, listed_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    listing_id, server_id, category, description, tags_json,
                    member_count, 0, VerificationLevel.NONE.value, 0, 0,
                    now, now, 0, user_id
                )
            )

        logger.info(f"Server {server_id} listed in category {category} by user {user_id}")

        return self._build_listing(
            listing_id, server_id, server_name, description, server_icon,
            None, category, tags or [], member_count, 0,
            VerificationLevel.NONE, False, False, now, now, 0
        )

    def unlist_server(self, user_id: int, server_id: int) -> bool:
        """
        Remove a server from the public directory.
        
        Args:
            user_id: ID of user unlisting server
            server_id: ID of server to unlist
            
        Returns:
            True if unlisted
        """
        self._check_list_permission(user_id, server_id)

        existing = self._get_listing(server_id)
        if not existing:
            raise ServerNotListedError(
                "Server is not listed in discovery",
                server_id=server_id
            )

        self._db.execute(
            "DELETE FROM search_server_listings WHERE server_id = ?",
            (server_id,)
        )

        logger.info(f"Server {server_id} unlisted by user {user_id}")

        return True

    def bump_server(self, user_id: int, server_id: int) -> bool:
        """
        Bump a server in the discovery listing.
        
        Args:
            user_id: ID of user bumping server
            server_id: ID of server to bump
            
        Returns:
            True if bumped
        """
        self._check_bump_permission(user_id, server_id)

        existing = self._get_listing(server_id)
        if not existing:
            raise ServerNotListedError(
                "Server is not listed in discovery",
                server_id=server_id
            )

        cooldown_remaining = self._check_bump_cooldown(server_id)
        if cooldown_remaining > 0:
            raise BumpCooldownError(
                f"Server can be bumped again in {cooldown_remaining // 1000} seconds",
                server_id=server_id,
                cooldown_remaining=cooldown_remaining
            )

        now = self._get_timestamp()

        self._db.execute(
            """UPDATE search_server_listings 
               SET bumped_at = ?, bump_count = bump_count + 1
               WHERE server_id = ?""",
            (now, server_id)
        )

        bump_id = self._generate_id()
        self._db.execute(
            """INSERT INTO search_bump_history (id, server_id, user_id, bumped_at)
               VALUES (?, ?, ?, ?)""",
            (bump_id, server_id, user_id, now)
        )

        logger.info(f"Server {server_id} bumped by user {user_id}")

        return True

    def list_public_servers(
        self,
        category: Optional[str] = None,
        sort_by: str = "member_count",
        limit: int = 25,
        offset: int = 0,
    ) -> List[ServerListing]:
        """
        List public servers in the discovery directory.
        
        Args:
            category: Optional category filter
            sort_by: Sort order (member_count, bumped_at, created_at)
            limit: Maximum results
            offset: Offset for pagination
            
        Returns:
            List of ServerListing objects
        """
        sort_column = {
            "member_count": "member_count DESC",
            "bumped_at": "bumped_at DESC",
            "created_at": "listed_at DESC",
        }.get(sort_by, "member_count DESC")

        sql = """
            SELECT l.*, s.name as server_name, s.icon_url as server_icon
            FROM search_server_listings l
            LEFT JOIN srv_servers s ON l.server_id = s.id
        """
        params = []

        if category:
            sql += " WHERE l.category = ?"
            params.append(category)

        sql += f" ORDER BY {sort_column} LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = self._db.fetch_all(sql, tuple(params))

        results = []
        for row in rows:
            tags = json.loads(row["tags"]) if row["tags"] else []
            results.append(self._build_listing(
                row["id"],
                row["server_id"],
                row.get("server_name", ""),
                row["description"],
                row.get("server_icon"),
                None,
                row["category"],
                tags,
                row["member_count"],
                row["online_count"],
                VerificationLevel(row["verification_level"]),
                bool(row["is_verified"]),
                bool(row["is_partnered"]),
                row["listed_at"],
                row["bumped_at"],
                row["bump_count"],
            ))

        return results

    def get_server_categories(self) -> List[ServerCategory]:
        """Get all available server categories."""
        self._categories.update_category_counts()
        return self._categories.get_all_categories()

    def get_listing(self, server_id: int) -> Optional[ServerListing]:
        """Get listing for a specific server."""
        row = self._db.fetch_one(
            """SELECT l.*, s.name as server_name, s.icon_url as server_icon
               FROM search_server_listings l
               LEFT JOIN srv_servers s ON l.server_id = s.id
               WHERE l.server_id = ?""",
            (server_id,)
        )

        if not row:
            return None

        tags = json.loads(row["tags"]) if row["tags"] else []
        return self._build_listing(
            row["id"],
            row["server_id"],
            row.get("server_name", ""),
            row["description"],
            row.get("server_icon"),
            None,
            row["category"],
            tags,
            row["member_count"],
            row["online_count"],
            VerificationLevel(row["verification_level"]),
            bool(row["is_verified"]),
            bool(row["is_partnered"]),
            row["listed_at"],
            row["bumped_at"],
            row["bump_count"],
        )

    def verify_server(self, server_id: int, level: VerificationLevel) -> bool:
        """Set verification level for a server."""
        return self._verification.verify_server(server_id, level)

    def _check_list_permission(self, user_id: int, server_id: int):
        """Check if user can list/unlist the server."""
        if not self._servers:
            return

        try:
            if not self._servers.has_permission(user_id, server_id, "server.manage"):
                raise SearchPermissionError(
                    "You need server management permission to list this server",
                    permission="server.manage"
                )
        except Exception as e:
            if isinstance(e, SearchPermissionError):
                raise
            raise SearchPermissionError(
                "Could not verify server permissions",
                permission="server.manage"
            )

    def _check_bump_permission(self, user_id: int, server_id: int):
        """Check if user can bump the server."""
        if not self._servers:
            return

        try:
            member = self._db.fetch_one(
                "SELECT 1 FROM srv_members WHERE server_id = ? AND user_id = ?",
                (server_id, user_id)
            )
            if not member:
                raise SearchPermissionError(
                    "You must be a member of the server to bump it",
                    permission="server.member"
                )
        except Exception as e:
            if isinstance(e, SearchPermissionError):
                raise

    def _check_bump_cooldown(self, server_id: int) -> int:
        """Check bump cooldown, return remaining time in ms or 0."""
        cooldown_hours = self._config.get("bump_cooldown_hours", 4)
        cooldown_ms = cooldown_hours * 60 * 60 * 1000

        now = self._get_timestamp()

        last_bump = self._db.fetch_one(
            """SELECT bumped_at FROM search_server_listings 
               WHERE server_id = ?""",
            (server_id,)
        )

        if not last_bump:
            return 0

        elapsed = now - last_bump["bumped_at"]
        if elapsed >= cooldown_ms:
            return 0

        return cooldown_ms - elapsed

    def _get_listing(self, server_id: int) -> Optional[Dict]:
        """Get raw listing from database."""
        return self._db.fetch_one(
            "SELECT * FROM search_server_listings WHERE server_id = ?",
            (server_id,)
        )

    def _get_member_count(self, server_id: int) -> int:
        """Get member count for a server."""
        row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM srv_members WHERE server_id = ?",
            (server_id,)
        )
        return row["count"] if row else 0

    def _get_server_info(self, server_id: int) -> Optional[Dict]:
        """Get server info from database."""
        return self._db.fetch_one(
            "SELECT * FROM srv_servers WHERE id = ?",
            (server_id,)
        )

    def _build_listing(
        self,
        listing_id: int,
        server_id: int,
        name: str,
        description: Optional[str],
        icon_url: Optional[str],
        banner_url: Optional[str],
        category: str,
        tags: List[str],
        member_count: int,
        online_count: int,
        verification_level: VerificationLevel,
        is_verified: bool,
        is_partnered: bool,
        listed_at: int,
        bumped_at: int,
        bump_count: int,
    ) -> ServerListing:
        """Build ServerListing object."""
        return ServerListing(
            id=listing_id,
            server_id=server_id,
            name=name or "",
            description=description or "",
            icon_url=icon_url,
            banner_url=banner_url,
            category=category,
            tags=tags,
            member_count=member_count,
            online_count=online_count,
            verification_level=verification_level,
            is_verified=is_verified,
            is_partnered=is_partnered,
            listed_at=listed_at,
            bumped_at=bumped_at,
            bump_count=bump_count,
        )
