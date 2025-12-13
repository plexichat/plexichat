"""
Presence manager - Core business logic for presence operations.

Handles user status, activities, typing indicators, and visibility rules
with proper validation and database interactions.
"""

import time
from typing import Optional, List, Dict

import utils.config as config
import utils.logger as logger
from src.utils.encryption import generate_snowflake_id

from .models import (
    Presence,
    UserStatus,
    Activity,
    ActivityType,
    TypingIndicator,
    CustomStatus,
)
from .exceptions import (
    UserNotFoundError,
    InvalidActivityError,
)
from .schema import create_tables


# Default typing indicator timeout in milliseconds (10 seconds)
DEFAULT_TYPING_TIMEOUT_MS = 10000

# Default presence expiry timeout in milliseconds (5 minutes)
DEFAULT_PRESENCE_TIMEOUT_MS = 300000


class PresenceManager:
    """Core presence manager handling all operations."""

    def __init__(self, db, auth_module=None, relationships_module=None, servers_module=None):
        """
        Initialize the presence manager.

        Args:
            db: Database instance (must be connected)
            auth_module: Optional auth module for user verification
            relationships_module: Optional relationships module for friend queries
            servers_module: Optional servers module for server member queries
        """
        self._db = db
        self._auth = auth_module
        self._relationships = relationships_module
        self._servers = servers_module

        # Load config
        self._typing_timeout_ms = config.get(
            "presence.typing_timeout_ms", DEFAULT_TYPING_TIMEOUT_MS
        )
        self._presence_timeout_ms = config.get(
            "presence.timeout_ms", DEFAULT_PRESENCE_TIMEOUT_MS
        )

        create_tables(db)

        logger.info("Presence module initialized")

    def _get_timestamp(self) -> int:
        """Get current timestamp in milliseconds."""
        return int(time.time() * 1000)

    def _generate_id(self) -> int:
        """Generate a new Snowflake ID."""
        return generate_snowflake_id()

    def _user_exists(self, user_id: int) -> bool:
        """Check if a user exists."""
        if self._auth:
            user = self._auth.get_user(user_id)
            return user is not None
        return True

    def _validate_user(self, user_id: int) -> None:
        """Validate user exists."""
        if not self._user_exists(user_id):
            raise UserNotFoundError(f"User {user_id} not found")

    def _ensure_presence_record(self, user_id: int) -> None:
        """Ensure a presence record exists for user."""
        now = self._get_timestamp()
        self._db.insert_or_ignore(
            "pres_presence",
            ["user_id", "status", "last_seen", "updated_at"],
            (user_id, "offline", now, now)
        )

    def _cleanup_expired_typing(self) -> None:
        """Remove expired typing indicators."""
        now = self._get_timestamp()
        self._db.execute(
            "DELETE FROM pres_typing WHERE expires_at < ?",
            (now,)
        )

    def _cleanup_expired_custom_status(self, user_id: int) -> None:
        """Remove expired custom status for user."""
        now = self._get_timestamp()
        self._db.execute(
            "DELETE FROM pres_custom_status WHERE user_id = ? AND expires_at IS NOT NULL AND expires_at < ?",
            (user_id, now)
        )

    # === Status Operations ===

    def set_status(self, user_id: int, status: UserStatus) -> Presence:
        """
        Set user's online status.
        
        Args:
            user_id: ID of the user
            status: New status to set
            
        Returns:
            Updated Presence
        """
        self._validate_user(user_id)
        self._ensure_presence_record(user_id)

        now = self._get_timestamp()

        self._db.execute(
            "UPDATE pres_presence SET status = ?, last_seen = ?, updated_at = ? WHERE user_id = ?",
            (status.value, now, now, user_id)
        )

        logger.debug(f"User {user_id} status set to {status.value}")

        return self.get_presence(user_id)

    def get_status(self, user_id: int) -> UserStatus:
        """Get user's current status."""
        row = self._db.fetch_one(
            "SELECT status FROM pres_presence WHERE user_id = ?",
            (user_id,)
        )

        if not row:
            return UserStatus.OFFLINE

        return UserStatus(row["status"])

    def clear_status(self, user_id: int) -> Presence:
        """Clear user's status (set to offline)."""
        return self.set_status(user_id, UserStatus.OFFLINE)

    # === Custom Status Operations ===

    def set_custom_status(
        self,
        user_id: int,
        text: str,
        emoji: Optional[str] = None,
        expires_at: Optional[int] = None
    ) -> Presence:
        """
        Set user's custom status message.
        
        Args:
            user_id: ID of the user
            text: Custom status text
            emoji: Optional emoji
            expires_at: Optional expiration timestamp in milliseconds
            
        Returns:
            Updated Presence
        """
        self._validate_user(user_id)
        self._ensure_presence_record(user_id)

        now = self._get_timestamp()

        self._db.upsert(
            "pres_custom_status",
            ["user_id", "text", "emoji", "expires_at", "created_at"],
            (user_id, text, emoji, expires_at, now),
            ["user_id"],
            ["text", "emoji", "expires_at"]
        )

        # Update presence timestamp
        self._db.execute(
            "UPDATE pres_presence SET updated_at = ? WHERE user_id = ?",
            (now, user_id)
        )

        logger.debug(f"User {user_id} custom status set")

        return self.get_presence(user_id)

    def get_custom_status(self, user_id: int) -> Optional[CustomStatus]:
        """Get user's custom status."""
        self._cleanup_expired_custom_status(user_id)

        row = self._db.fetch_one(
            "SELECT * FROM pres_custom_status WHERE user_id = ?",
            (user_id,)
        )

        if not row:
            return None

        return CustomStatus(
            text=row["text"],
            emoji=row["emoji"],
            expires_at=row["expires_at"],
            created_at=row["created_at"]
        )

    def clear_custom_status(self, user_id: int) -> Presence:
        """Clear user's custom status."""
        self._validate_user(user_id)

        self._db.execute(
            "DELETE FROM pres_custom_status WHERE user_id = ?",
            (user_id,)
        )

        now = self._get_timestamp()
        self._db.execute(
            "UPDATE pres_presence SET updated_at = ? WHERE user_id = ?",
            (now, user_id)
        )

        return self.get_presence(user_id)

    # === Activity Operations ===

    def set_activity(
        self,
        user_id: int,
        activity_type: ActivityType,
        name: str,
        details: Optional[str] = None,
        url: Optional[str] = None,
        state: Optional[str] = None,
        timestamps: Optional[Dict[str, int]] = None,
        assets: Optional[Dict[str, str]] = None
    ) -> Presence:
        """
        Set user's current activity.
        
        Args:
            user_id: ID of the user
            activity_type: Type of activity
            name: Activity name (e.g., game name, song title)
            details: Optional details
            url: Optional URL (for streaming)
            state: Optional state text
            timestamps: Optional dict with 'start' and/or 'end' timestamps
            assets: Optional dict with image keys (large_image, large_text, etc.)
            
        Returns:
            Updated Presence
        """
        self._validate_user(user_id)
        self._ensure_presence_record(user_id)

        if not name or not name.strip():
            raise InvalidActivityError("Activity name cannot be empty")

        now = self._get_timestamp()

        start_ts = timestamps.get("start") if timestamps else None
        end_ts = timestamps.get("end") if timestamps else None
        large_image = assets.get("large_image") if assets else None
        large_text = assets.get("large_text") if assets else None
        small_image = assets.get("small_image") if assets else None
        small_text = assets.get("small_text") if assets else None

        self._db.upsert(
            "pres_activity",
            ["user_id", "activity_type", "name", "details", "url", "state",
             "start_timestamp", "end_timestamp", "large_image", "large_text",
             "small_image", "small_text", "created_at"],
            (user_id, activity_type.value, name, details, url, state,
             start_ts, end_ts, large_image, large_text, small_image, small_text, now),
            ["user_id"],
            ["activity_type", "name", "details", "url", "state",
             "start_timestamp", "end_timestamp", "large_image", "large_text",
             "small_image", "small_text"]
        )

        self._db.execute(
            "UPDATE pres_presence SET updated_at = ? WHERE user_id = ?",
            (now, user_id)
        )

        logger.debug(f"User {user_id} activity set to {activity_type.value}: {name}")

        return self.get_presence(user_id)

    def get_activity(self, user_id: int) -> Optional[Activity]:
        """Get user's current activity."""
        row = self._db.fetch_one(
            "SELECT * FROM pres_activity WHERE user_id = ?",
            (user_id,)
        )

        if not row:
            return None

        return Activity(
            activity_type=ActivityType(row["activity_type"]),
            name=row["name"],
            details=row["details"],
            url=row["url"],
            state=row["state"],
            start_timestamp=row["start_timestamp"],
            end_timestamp=row["end_timestamp"],
            large_image=row["large_image"],
            large_text=row["large_text"],
            small_image=row["small_image"],
            small_text=row["small_text"],
            created_at=row["created_at"]
        )

    def clear_activity(self, user_id: int) -> Presence:
        """Clear user's current activity."""
        self._validate_user(user_id)

        self._db.execute(
            "DELETE FROM pres_activity WHERE user_id = ?",
            (user_id,)
        )

        now = self._get_timestamp()
        self._db.execute(
            "UPDATE pres_presence SET updated_at = ? WHERE user_id = ?",
            (now, user_id)
        )

        return self.get_presence(user_id)

    # === Presence Operations ===

    def get_presence(self, user_id: int) -> Presence:
        """Get full presence information for a user."""
        row = self._db.fetch_one(
            "SELECT * FROM pres_presence WHERE user_id = ?",
            (user_id,)
        )

        if not row:
            return Presence(
                user_id=user_id,
                status=UserStatus.OFFLINE,
                custom_status=None,
                activity=None,
                last_seen=0,
                updated_at=0
            )

        custom_status = self.get_custom_status(user_id)
        activity = self.get_activity(user_id)

        return Presence(
            user_id=user_id,
            status=UserStatus(row["status"]),
            custom_status=custom_status,
            activity=activity,
            last_seen=row["last_seen"],
            updated_at=row["updated_at"]
        )

    def get_presences(self, user_ids: List[int]) -> List[Presence]:
        """Get presence information for multiple users efficiently with batch queries."""
        if not user_ids:
            return []

        # Batch fetch all presence records in a single query
        placeholders = ",".join("?" * len(user_ids))
        presence_rows = self._db.fetch_all(
            f"SELECT * FROM pres_presence WHERE user_id IN ({placeholders})",
            tuple(user_ids)
        )
        presence_map = {row["user_id"]: row for row in presence_rows}

        # Batch fetch all custom statuses
        self._cleanup_expired_custom_status_batch(user_ids)
        custom_rows = self._db.fetch_all(
            f"SELECT * FROM pres_custom_status WHERE user_id IN ({placeholders})",
            tuple(user_ids)
        )
        custom_map = {row["user_id"]: row for row in custom_rows}

        # Batch fetch all activities
        activity_rows = self._db.fetch_all(
            f"SELECT * FROM pres_activity WHERE user_id IN ({placeholders})",
            tuple(user_ids)
        )
        activity_map = {row["user_id"]: row for row in activity_rows}

        # Build presence objects
        results = []
        for uid in user_ids:
            pres_row = presence_map.get(uid)
            if not pres_row:
                results.append(Presence(
                    user_id=uid,
                    status=UserStatus.OFFLINE,
                    custom_status=None,
                    activity=None,
                    last_seen=0,
                    updated_at=0
                ))
                continue

            # Build custom status if exists
            custom_status = None
            custom_row = custom_map.get(uid)
            if custom_row:
                custom_status = CustomStatus(
                    text=custom_row["text"],
                    emoji=custom_row["emoji"],
                    expires_at=custom_row["expires_at"],
                    created_at=custom_row["created_at"]
                )

            # Build activity if exists
            activity = None
            activity_row = activity_map.get(uid)
            if activity_row:
                activity = Activity(
                    activity_type=ActivityType(activity_row["activity_type"]),
                    name=activity_row["name"],
                    details=activity_row["details"],
                    url=activity_row["url"],
                    state=activity_row["state"],
                    start_timestamp=activity_row["start_timestamp"],
                    end_timestamp=activity_row["end_timestamp"],
                    large_image=activity_row["large_image"],
                    large_text=activity_row["large_text"],
                    small_image=activity_row["small_image"],
                    small_text=activity_row["small_text"],
                    created_at=activity_row["created_at"]
                )

            results.append(Presence(
                user_id=uid,
                status=UserStatus(pres_row["status"]),
                custom_status=custom_status,
                activity=activity,
                last_seen=pres_row["last_seen"],
                updated_at=pres_row["updated_at"]
            ))

        return results

    def _cleanup_expired_custom_status_batch(self, user_ids: List[int]) -> None:
        """Remove expired custom statuses for multiple users."""
        if not user_ids:
            return
        now = self._get_timestamp()
        placeholders = ",".join("?" * len(user_ids))
        self._db.execute(
            f"DELETE FROM pres_custom_status WHERE user_id IN ({placeholders}) AND expires_at IS NOT NULL AND expires_at < ?",
            tuple(user_ids) + (now,)
        )

    def update_last_seen(self, user_id: int) -> Presence:
        """Update user's last seen timestamp."""
        self._validate_user(user_id)
        self._ensure_presence_record(user_id)

        now = self._get_timestamp()

        self._db.execute(
            "UPDATE pres_presence SET last_seen = ?, updated_at = ? WHERE user_id = ?",
            (now, now, user_id)
        )

        return self.get_presence(user_id)

    # === Typing Indicators ===

    def start_typing(self, user_id: int, channel_id: int) -> TypingIndicator:
        """
        Start typing indicator in a channel.
        
        Args:
            user_id: ID of the user typing
            channel_id: ID of the channel
            
        Returns:
            TypingIndicator
        """
        self._validate_user(user_id)
        self._cleanup_expired_typing()

        now = self._get_timestamp()
        expires_at = now + self._typing_timeout_ms

        indicator_id = self._generate_id()

        self._db.upsert(
            "pres_typing",
            ["id", "user_id", "channel_id", "started_at", "expires_at"],
            (indicator_id, user_id, channel_id, now, expires_at),
            ["user_id", "channel_id"],
            ["id", "started_at", "expires_at"]
        )

        logger.debug(f"User {user_id} started typing in channel {channel_id}")

        return TypingIndicator(
            user_id=user_id,
            channel_id=channel_id,
            started_at=now,
            expires_at=expires_at
        )

    def stop_typing(self, user_id: int, channel_id: int) -> bool:
        """
        Stop typing indicator in a channel.
        
        Args:
            user_id: ID of the user
            channel_id: ID of the channel
            
        Returns:
            True if indicator was removed
        """
        self._db.execute(
            "DELETE FROM pres_typing WHERE user_id = ? AND channel_id = ?",
            (user_id, channel_id)
        )

        logger.debug(f"User {user_id} stopped typing in channel {channel_id}")

        return True

    def get_typing_users(self, channel_id: int) -> List[TypingIndicator]:
        """Get users currently typing in a channel."""
        self._cleanup_expired_typing()

        rows = self._db.fetch_all(
            "SELECT * FROM pres_typing WHERE channel_id = ?",
            (channel_id,)
        )

        return [
            TypingIndicator(
                user_id=row["user_id"],
                channel_id=row["channel_id"],
                started_at=row["started_at"],
                expires_at=row["expires_at"]
            )
            for row in rows
        ]

    # === Online Queries ===

    def get_online_friends(self, user_id: int) -> List[int]:
        """
        Get list of online friend user IDs.
        
        Args:
            user_id: ID of the user
            
        Returns:
            List of online friend user IDs
        """
        if not self._relationships:
            return []

        friend_ids = self._relationships.get_friend_ids(user_id)
        if not friend_ids:
            return []

        online_statuses = [UserStatus.ONLINE.value, UserStatus.IDLE.value, UserStatus.DND.value]

        placeholders = ",".join("?" * len(friend_ids))
        status_placeholders = ",".join("?" * len(online_statuses))

        rows = self._db.fetch_all(
            f"""SELECT user_id FROM pres_presence 
                WHERE user_id IN ({placeholders}) 
                AND status IN ({status_placeholders})""",
            tuple(friend_ids) + tuple(online_statuses)
        )

        return [row["user_id"] for row in rows]

    def get_online_server_members(self, user_id: int, server_id: int) -> List[int]:
        """
        Get list of online member user IDs in a server.
        
        Args:
            user_id: ID of the requesting user
            server_id: ID of the server
            
        Returns:
            List of online member user IDs
        """
        if not self._servers:
            return []

        members = self._servers.get_members(user_id, server_id)
        if not members:
            return []

        member_ids = [m.user_id for m in members]
        online_statuses = [UserStatus.ONLINE.value, UserStatus.IDLE.value, UserStatus.DND.value]

        placeholders = ",".join("?" * len(member_ids))
        status_placeholders = ",".join("?" * len(online_statuses))

        rows = self._db.fetch_all(
            f"""SELECT user_id FROM pres_presence 
                WHERE user_id IN ({placeholders}) 
                AND status IN ({status_placeholders})""",
            tuple(member_ids) + tuple(online_statuses)
        )

        return [row["user_id"] for row in rows]

    # === Visibility ===

    def get_visible_presence(self, viewer_id: int, target_id: int) -> Presence:
        """
        Get presence as visible to a specific viewer.
        
        Respects invisible mode and block relationships.
        Blocked users see target as offline.
        Invisible users appear offline to others.
        
        Args:
            viewer_id: ID of the user viewing
            target_id: ID of the user being viewed
            
        Returns:
            Presence (may show offline if invisible or blocked)
        """
        presence = self.get_presence(target_id)

        # User viewing themselves always sees real status
        if viewer_id == target_id:
            return presence

        # Check if blocked
        if self._relationships:
            if self._relationships.is_blocked_by_either(viewer_id, target_id):
                return Presence(
                    user_id=target_id,
                    status=UserStatus.OFFLINE,
                    custom_status=None,
                    activity=None,
                    last_seen=0,
                    updated_at=0
                )

        # Invisible users appear offline to others
        if presence.status == UserStatus.INVISIBLE:
            return Presence(
                user_id=target_id,
                status=UserStatus.OFFLINE,
                custom_status=presence.custom_status,
                activity=None,
                last_seen=presence.last_seen,
                updated_at=presence.updated_at
            )

        return presence

    def can_see_presence(self, viewer_id: int, target_id: int) -> bool:
        """
        Check if viewer can see target's real presence.
        
        Args:
            viewer_id: ID of the user viewing
            target_id: ID of the user being viewed
            
        Returns:
            True if viewer can see real presence
        """
        # User can always see their own presence
        if viewer_id == target_id:
            return True

        # Check if blocked
        if self._relationships:
            if self._relationships.is_blocked_by_either(viewer_id, target_id):
                return False

        # Check if target is invisible
        status = self.get_status(target_id)
        if status == UserStatus.INVISIBLE:
            return False

        return True
