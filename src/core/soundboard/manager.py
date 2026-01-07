"""
Soundboard manager - Core business logic for soundboard operations.

Handles sound upload, permissions, cooldowns, and playback triggering
with proper validation and permission checks.
"""

import re
from typing import Optional, List, Dict, Any

import utils.config as config
import utils.logger as logger
from src.core.base import BaseManager

from .models import (
    Sound,
    SoundPermissions,
    SoundPlayback,
    SoundFormat,
)
from .exceptions import (
    SoundNotFoundError,
    SoundLimitError,
    InvalidSoundFormatError,
    SoundTooLargeError,
    SoundTooLongError,
    InvalidSoundNameError,
    SoundCooldownError,
    PermissionDeniedError,
    ChannelNotFoundError,
)
from .schema import create_tables


class SoundboardManager(BaseManager):
    """Core soundboard manager handling all operations."""

    def __init__(self, db, auth_module=None, servers_module=None):
        """
        Initialize the soundboard manager.

        Args:
            db: Database instance (must be connected)
            auth_module: Optional auth module for user verification
            servers_module: Optional servers module for permission checks
        """
        super().__init__(db, auth_module)
        self._servers = servers_module
        self._config = self._load_config()
        self._cooldowns = {}

        create_tables(db)

        logger.info("Soundboard module initialized")

    def _load_config(self) -> Dict[str, Any]:
        """Load soundboard configuration from global config."""
        return config.get("soundboard", {})

    def _validate_sound_name(self, name: str) -> str:
        """Validate and sanitize sound name."""
        if not name or not name.strip():
            raise InvalidSoundNameError("Sound name cannot be empty")

        name = name.strip()
        max_len = self._config.get("max_sound_name_length", 30)

        if len(name) > max_len:
            raise InvalidSoundNameError(
                f"Sound name cannot exceed {max_len} characters"
            )

        if not re.match(r"^[a-zA-Z0-9_\-]+$", name):
            raise InvalidSoundNameError(
                "Sound name can only contain letters, numbers, underscores, and hyphens"
            )

        return name

    def _check_server_permission(self, user_id: int, server_id: int) -> bool:
        """Check if user has manage server permission."""
        if not self._servers:
            return True
        return self._servers.has_permission(user_id, server_id, "server.manage")

    def _is_server_member(self, user_id: int, server_id: int) -> bool:
        """Check if user is a member of the server."""
        if not self._servers:
            return True
        member = self._servers.get_member(server_id, user_id)
        return member is not None

    def _get_user_roles(self, user_id: int, server_id: int) -> List[int]:
        """Get user's role IDs in a server."""
        if not self._servers:
            return []
        roles = self._servers.get_member_roles(server_id, user_id)
        return [role.id for role in roles]

    def _check_cooldown(
        self, user_id: int, sound_id: int, cooldown_seconds: int
    ) -> Optional[int]:
        """
        Check if sound is on cooldown for user.

        Returns:
            None if not on cooldown, remaining seconds if on cooldown
        """
        key = (user_id, sound_id)
        if key not in self._cooldowns:
            return None

        last_used = self._cooldowns[key]
        now = self._get_timestamp()
        elapsed_ms = now - last_used
        elapsed_seconds = elapsed_ms / 1000

        if elapsed_seconds >= cooldown_seconds:
            del self._cooldowns[key]
            return None

        remaining = int(cooldown_seconds - elapsed_seconds)
        return remaining

    def _set_cooldown(self, user_id: int, sound_id: int):
        """Set cooldown for user and sound."""
        key = (user_id, sound_id)
        self._cooldowns[key] = self._get_timestamp()

    def upload_sound(
        self,
        user_id: int,
        server_id: int,
        name: str,
        format: SoundFormat,
        url: str,
        size: int,
        duration_seconds: float,
        emoji: Optional[str] = None,
        volume: float = 1.0,
    ) -> Sound:
        """
        Upload a sound to server soundboard.

        Args:
            user_id: ID of user uploading sound
            server_id: ID of server
            name: Sound name
            format: Sound format
            url: Sound URL
            size: File size in bytes
            duration_seconds: Sound duration
            emoji: Optional emoji
            volume: Volume level (0.0-1.0)

        Returns:
            Created Sound

        Raises:
            InvalidSoundNameError: Invalid sound name
            InvalidSoundFormatError: Invalid format
            SoundTooLargeError: File too large
            SoundTooLongError: Duration too long
            SoundLimitError: Maximum sounds reached
            PermissionDeniedError: No permission
            ServerNotFoundError: Server not found
        """
        if not self._check_server_permission(user_id, server_id):
            raise PermissionDeniedError(
                "Missing permission to manage server", "server.manage"
            )

        name = self._validate_sound_name(name)

        allowed_formats = self._config.get("allowed_formats", ["mp3", "ogg"])
        if format.value not in allowed_formats:
            raise InvalidSoundFormatError(
                f"Format {format.value} not allowed", format.value, allowed_formats
            )

        max_size = self._config.get("max_sound_size", 524288)
        if size > max_size:
            raise SoundTooLargeError(
                f"Sound exceeds maximum size of {max_size} bytes", max_size, size
            )

        max_duration = self._config.get("max_sound_duration_seconds", 5)
        if duration_seconds > max_duration:
            raise SoundTooLongError(
                f"Sound exceeds maximum duration of {max_duration} seconds",
                max_duration,
                duration_seconds,
            )

        if volume < 0.0 or volume > 1.0:
            volume = max(0.0, min(1.0, volume))

        count_row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM soundboard_sounds WHERE server_id = ?",
            (server_id,),
        )
        max_sounds = self._config.get("max_sounds_per_server", 100)
        if count_row and count_row["count"] >= max_sounds:
            raise SoundLimitError(
                f"Server has reached maximum of {max_sounds} sounds",
                max_sounds,
                count_row["count"],
            )

        now = self._get_timestamp()
        sound_id = self._generate_id()

        self._db.execute(
            """INSERT INTO soundboard_sounds 
               (id, server_id, name, format, emoji, url, size, duration_seconds, volume, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                sound_id,
                server_id,
                name,
                format.value,
                emoji,
                url,
                size,
                duration_seconds,
                volume,
                user_id,
                now,
            ),
        )

        logger.debug(f"Uploaded sound {sound_id} to server {server_id}")

        result = self.get_sound(sound_id, user_id)
        assert result is not None  # Should exist since we just created it
        return result

    def get_sound(self, sound_id: int, user_id: int) -> Optional[Sound]:
        """Get a sound by ID."""
        row = self._db.fetch_one(
            """SELECT s.*, COUNT(u.id) as usage_count
               FROM soundboard_sounds s
               LEFT JOIN soundboard_usage u ON s.id = u.sound_id
               WHERE s.id = ?
               GROUP BY s.id""",
            (sound_id,),
        )

        if not row:
            return None

        if not self._is_server_member(user_id, row["server_id"]):
            return None

        return self._row_to_sound(row)

    def get_server_sounds(self, user_id: int, server_id: int) -> List[Sound]:
        """Get all sounds for a server."""
        if not self._is_server_member(user_id, server_id):
            raise PermissionDeniedError("Not a member of this server")

        rows = self._db.fetch_all(
            """SELECT s.*, COUNT(u.id) as usage_count
               FROM soundboard_sounds s
               LEFT JOIN soundboard_usage u ON s.id = u.sound_id
               WHERE s.server_id = ?
               GROUP BY s.id
               ORDER BY s.name""",
            (server_id,),
        )

        return [self._row_to_sound(row) for row in rows]

    def delete_sound(self, user_id: int, sound_id: int) -> bool:
        """
        Delete a sound.

        Args:
            user_id: ID of user deleting sound
            sound_id: ID of sound to delete

        Returns:
            True if deleted

        Raises:
            SoundNotFoundError: Sound not found
            PermissionDeniedError: No permission
        """
        sound = self.get_sound(sound_id, user_id)
        if not sound:
            raise SoundNotFoundError("Sound not found")

        if not self._check_server_permission(user_id, sound.server_id):
            raise PermissionDeniedError(
                "Missing permission to manage server", "server.manage"
            )

        self._db.execute(
            "DELETE FROM soundboard_permissions WHERE sound_id = ?", (sound_id,)
        )
        self._db.execute("DELETE FROM soundboard_sounds WHERE id = ?", (sound_id,))

        logger.debug(f"Deleted sound {sound_id}")
        return True

    def set_sound_permissions(
        self, user_id: int, sound_id: int, role_id: int, can_use: bool
    ) -> SoundPermissions:
        """
        Set sound usage permissions for a role.

        Args:
            user_id: ID of user setting permissions
            sound_id: ID of sound
            role_id: ID of role
            can_use: Whether role can use the sound

        Returns:
            SoundPermissions

        Raises:
            SoundNotFoundError: Sound not found
            PermissionDeniedError: No permission
        """
        sound = self.get_sound(sound_id, user_id)
        if not sound:
            raise SoundNotFoundError("Sound not found")

        if not self._check_server_permission(user_id, sound.server_id):
            raise PermissionDeniedError(
                "Missing permission to manage server", "server.manage"
            )

        existing = self._db.fetch_one(
            "SELECT id FROM soundboard_permissions WHERE sound_id = ? AND role_id = ?",
            (sound_id, role_id),
        )

        if existing:
            self._db.execute(
                "UPDATE soundboard_permissions SET can_use = ? WHERE id = ?",
                (1 if can_use else 0, existing["id"]),
            )
            perm_id = existing["id"]
        else:
            perm_id = self._generate_id()
            self._db.execute(
                """INSERT INTO soundboard_permissions (id, sound_id, role_id, can_use)
                   VALUES (?, ?, ?, ?)""",
                (perm_id, sound_id, role_id, 1 if can_use else 0),
            )

        return SoundPermissions(
            id=perm_id, sound_id=sound_id, role_id=role_id, can_use=can_use
        )

    def _can_use_sound(self, user_id: int, sound_id: int, server_id: int) -> bool:
        """Check if user can use a sound based on role permissions."""
        user_roles = self._get_user_roles(user_id, server_id)
        if not user_roles:
            return True

        perms = self._db.fetch_all(
            "SELECT role_id, can_use FROM soundboard_permissions WHERE sound_id = ?",
            (sound_id,),
        )

        if not perms:
            return True

        for perm in perms:
            if perm["role_id"] in user_roles:
                if not perm["can_use"]:
                    return False

        return True

    def play_sound(self, user_id: int, sound_id: int, channel_id: int) -> SoundPlayback:
        """
        Play a sound in a voice channel.

        This triggers a playback event that the voice module should handle.

        Args:
            user_id: ID of user playing sound
            sound_id: ID of sound
            channel_id: ID of voice channel

        Returns:
            SoundPlayback event

        Raises:
            SoundNotFoundError: Sound not found
            ChannelNotFoundError: Channel not found or not voice
            PermissionDeniedError: No permission to use sound
            SoundCooldownError: Sound is on cooldown
        """
        sound = self.get_sound(sound_id, user_id)
        if not sound:
            raise SoundNotFoundError("Sound not found")

        if self._servers:
            channel = self._servers.get_channel(channel_id, user_id)
            if not channel:
                raise ChannelNotFoundError("Channel not found")
            if channel.channel_type.value != "voice":
                raise ChannelNotFoundError("Channel is not a voice channel")

        if not self._can_use_sound(user_id, sound_id, sound.server_id):
            raise PermissionDeniedError("You do not have permission to use this sound")

        cooldown_seconds = self._config.get("default_cooldown_seconds", 5)
        remaining = self._check_cooldown(user_id, sound_id, cooldown_seconds)
        if remaining is not None:
            raise SoundCooldownError(
                f"Sound is on cooldown for {remaining} more seconds", remaining
            )

        now = self._get_timestamp()
        usage_id = self._generate_id()

        self._db.execute(
            """INSERT INTO soundboard_usage (id, sound_id, user_id, channel_id, used_at)
               VALUES (?, ?, ?, ?, ?)""",
            (usage_id, sound_id, user_id, channel_id, now),
        )

        self._set_cooldown(user_id, sound_id)

        logger.debug(
            f"Sound {sound_id} played by user {user_id} in channel {channel_id}"
        )

        return SoundPlayback(
            sound=sound, user_id=user_id, channel_id=channel_id, timestamp=now
        )

    def _row_to_sound(self, row) -> Sound:
        """Convert database row to Sound."""
        usage_count = 0
        try:
            usage_count = row["usage_count"]
        except (KeyError, IndexError):
            # usage_count may not be present in all queries
            usage_count = 0

        return Sound(
            id=row["id"],
            server_id=row["server_id"],
            name=row["name"],
            format=SoundFormat(row["format"]),
            emoji=row["emoji"],
            url=row["url"],
            size=row["size"],
            duration_seconds=row["duration_seconds"],
            volume=row["volume"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            usage_count=usage_count,
        )
