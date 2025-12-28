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
from src.core.database import cache_get, cache_set, cache_delete, redis_available, cache_presence, get_cached_presence


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

        # Invalidate/Update cache
        if redis_available():
            # We fetch full presence to cache it
            presence = self.get_presence(user_id, use_cache=False)
            cache_set(f"presence:{user_id}", self._presence_to_dict(presence), ttl=self._presence_timeout_ms // 1000)

        # Invalidate/Update cache
        if redis_available():
            presence = self.get_presence(user_id, use_cache=False)
            cache_set(f"presence:{user_id}", self._presence_to_dict(presence), ttl=self._presence_timeout_ms // 1000)

        logger.debug(f"User {user_id} status set to {status.value}")

        result = self.get_presence(user_id)
        return result

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

        result = self.get_presence(user_id)
        return result

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

        result = self.get_presence(user_id)
        return result

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

        if redis_available():
            presence = self.get_presence(user_id, use_cache=False)
            cache_set(f"presence:{user_id}", self._presence_to_dict(presence), ttl=self._presence_timeout_ms // 1000)

        logger.debug(f"User {user_id} activity set to {activity_type.value}: {name}")

        result = self.get_presence(user_id)
        return result

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

        if redis_available():
            presence = self.get_presence(user_id, use_cache=False)
            cache_set(f"presence:{user_id}", self._presence_to_dict(presence), ttl=self._presence_timeout_ms // 1000)

        result = self.get_presence(user_id)
        return result

    # === Presence Operations ===

    def get_presence(self, user_id: int, use_cache: bool = True) -> Presence:
        """Get full presence information for a user."""
        if use_cache and redis_available():
            cached = get_cached_presence(user_id)
            if cached:
                return self._dict_to_presence(cached)

        row = self._db.fetch_one(
            "SELECT * FROM pres_presence WHERE user_id = ?",
            (user_id,)
        )

        if not row:
            presence = Presence(
                user_id=user_id,
                status=UserStatus.OFFLINE,
                custom_status=None,
                activity=None,
                last_seen=0,
                updated_at=0
            )
        else:
            custom_status = self.get_custom_status(user_id)
            activity = self.get_activity(user_id)

            presence = Presence(
                user_id=user_id,
                status=UserStatus(row["status"]),
                custom_status=custom_status,
                activity=activity,
                last_seen=row["last_seen"],
                updated_at=row["updated_at"]
            )
            
        # Cache the result
        if use_cache and redis_available() and row:
             cache_set(f"presence:{user_id}", self._presence_to_dict(presence), ttl=self._presence_timeout_ms // 1000)
             
        return presence

    def get_presences(self, user_ids: List[int]) -> List[Presence]:
        """Get presence information for multiple users efficiently with batch queries and Redis caching."""
        if not user_ids:
            return []

        results_map: Dict[int, Presence] = {}
        missing_ids = list(user_ids)

        # 1. Try to fetch from Redis bulk
        if redis_available():
            try:
                from src.core.database.cache import get_bulk_presence
                cached_data = get_bulk_presence(user_ids)
                for uid, data in cached_data.items():
                    try:
                        results_map[uid] = self._dict_to_presence(data)
                        missing_ids.remove(uid)
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"Failed to fetch bulk presence from Redis: {e}")

        if not missing_ids:
            return [results_map[uid] for uid in user_ids]

        # 2. Batch fetch missing from DB
        placeholders = ",".join("?" * len(missing_ids))
        presence_rows = self._db.fetch_all(
            f"SELECT * FROM pres_presence WHERE user_id IN ({placeholders})",
            tuple(missing_ids)
        )
        presence_map = {row["user_id"]: row for row in presence_rows}

        # Batch fetch all custom statuses
        self._cleanup_expired_custom_status_batch(missing_ids)
        custom_rows = self._db.fetch_all(
            f"SELECT * FROM pres_custom_status WHERE user_id IN ({placeholders})",
            tuple(missing_ids)
        )
        custom_map = {row["user_id"]: row for row in custom_rows}

        # Batch fetch all activities
        activity_rows = self._db.fetch_all(
            f"SELECT * FROM pres_activity WHERE user_id IN ({placeholders})",
            tuple(missing_ids)
        )
        activity_map = {row["user_id"]: row for row in activity_rows}

        # Build presence objects and cache them
        for uid in missing_ids:
            pres_row = presence_map.get(uid)
            if not pres_row:
                presence = Presence(
                    user_id=uid,
                    status=UserStatus.OFFLINE,
                    custom_status=None,
                    activity=None,
                    last_seen=0,
                    updated_at=0
                )
            else:
                cust_row = custom_map.get(uid)
                custom_status = CustomStatus(
                    text=cust_row["text"],
                    emoji=cust_row["emoji"],
                    expires_at=cust_row["expires_at"]
                ) if cust_row else None

                act_row = activity_map.get(uid)
                activity = Activity(
                    activity_type=ActivityType(act_row["activity_type"]),
                    name=act_row["name"],
                    details=act_row["details"],
                    url=act_row["url"],
                    state=act_row["state"],
                    start_timestamp=act_row["start_timestamp"],
                    end_timestamp=act_row["end_timestamp"],
                    large_image=act_row["large_image"],
                    large_text=act_row["large_text"],
                    small_image=act_row["small_image"],
                    small_text=act_row["small_text"],
                    created_at=act_row["created_at"]
                ) if act_row else None

                presence = Presence(
                    user_id=uid,
                    status=UserStatus(pres_row["status"]),
                    custom_status=custom_status,
                    activity=activity,
                    last_seen=pres_row["last_seen"],
                    updated_at=pres_row["updated_at"]
                )
            
            results_map[uid] = presence
            
            # Cache missing ones
            if redis_available() and pres_row:
                 cache_set(f"presence:{uid}", self._presence_to_dict(presence), ttl=self._presence_timeout_ms // 1000)

        return [results_map[uid] for uid in user_ids]


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

        result = self.get_presence(user_id)
        return result

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
    def _presence_to_dict(self, presence: Presence) -> Dict[str, Any]:
        """Convert Presence model to dict for caching."""
        return {
            "user_id": presence.user_id,
            "status": presence.status.value,
            "custom_status": {
                "text": presence.custom_status.text,
                "emoji": presence.custom_status.emoji,
                "expires_at": presence.custom_status.expires_at
            } if presence.custom_status else None,
            "activity": {
                "activity_type": presence.activity.activity_type.value,
                "name": presence.activity.name,
                "details": presence.activity.details,
                "url": presence.activity.url,
                "state": presence.activity.state,
                "start_timestamp": presence.activity.start_timestamp,
                "end_timestamp": presence.activity.end_timestamp,
                "large_image": presence.activity.large_image,
                "large_text": presence.activity.large_text,
                "small_image": presence.activity.small_image,
                "small_text": presence.activity.small_text,
                "created_at": presence.activity.created_at
            } if presence.activity else None,
            "last_seen": presence.last_seen,
            "updated_at": presence.updated_at
        }

    def _dict_to_presence(self, data: Dict[str, Any]) -> Presence:
        """Convert cached dict to Presence model."""
        custom_status = data.get("custom_status")
        activity = data.get("activity")
        
        return Presence(
            user_id=data["user_id"],
            status=UserStatus(data["status"]),
            custom_status=CustomStatus(
                text=custom_status["text"],
                emoji=custom_status["emoji"],
                expires_at=custom_status["expires_at"]
            ) if custom_status else None,
            activity=Activity(
                activity_type=ActivityType(activity["activity_type"]),
                name=activity["name"],
                details=activity.get("details"),
                url=activity.get("url"),
                state=activity.get("state"),
                start_timestamp=activity.get("start_timestamp"),
                end_timestamp=activity.get("end_timestamp"),
                large_image=activity.get("large_image"),
                large_text=activity.get("large_text"),
                small_image=activity.get("small_image"),
                small_text=activity.get("small_text"),
                created_at=activity.get("created_at", 0)
            ) if activity else None,
            last_seen=data.get("last_seen", 0),
            updated_at=data.get("updated_at", 0)
        )
