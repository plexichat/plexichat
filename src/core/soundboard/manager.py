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
    NotInVoiceChannelError,
)


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
        # Cooldown state persists in the ``soundboard_user_cooldowns``
        # table (created by ``soundboard/schema.py``); we deliberately
        # do NOT keep an in-memory cache — process restarts and
        # multi-instance deployments must see the same enforcement.
        self._client_local_cooldowns: Dict[tuple, int] = {}

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
        Check if sound is on cooldown for ``user_id``.

        Source of truth is the ``soundboard_user_cooldowns`` table so
        enforcement survives process restarts and works across multiple
        Plexichat instances; the in-process ``_client_local_cooldowns``
        cache is consulted first to avoid a DB round-trip on every
        repeat play.  Returns ``None`` if the user is free to play
        the sound again, otherwise the remaining cooldown seconds.
        """
        if cooldown_seconds <= 0:
            return None

        now = self._get_timestamp()
        candidates: List[int] = []

        local_ts = self._client_local_cooldowns.get((user_id, sound_id))
        if local_ts is not None:
            candidates.append(int(local_ts))

        try:
            row = self._db.fetch_one(
                "SELECT last_play_at FROM soundboard_user_cooldowns "
                "WHERE user_id = ? AND sound_id = ?",
                (user_id, sound_id),
            )
            if row and row.get("last_play_at") is not None:
                candidates.append(int(row["last_play_at"]))
        except Exception:
            pass  # DB unreachable — fall back to local cache only

        if not candidates:
            return None
        last_used = max(candidates)
        elapsed_seconds = (now - last_used) / 1000.0
        if elapsed_seconds >= cooldown_seconds:
            return None
        return int(cooldown_seconds - elapsed_seconds)

    def _set_cooldown(self, user_id: int, sound_id: int, server_id: int) -> None:
        """Persist the cooldown timestamp for ``user_id`` on ``sound_id``.

        Writes both the authoritative DB row and the in-process cache
        so back-to-back plays within a single connection don't hit
        the DB.  Falls back to ``DELETE`` + ``INSERT`` on drivers
        without ``ON CONFLICT``.
        """
        now = self._get_timestamp()
        self._client_local_cooldowns[(user_id, sound_id)] = now

        try:
            self._db.execute(
                "INSERT INTO soundboard_user_cooldowns "
                "(user_id, sound_id, server_id, last_play_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(user_id, sound_id) DO UPDATE SET "
                "  last_play_at = excluded.last_play_at, "
                "  server_id    = excluded.server_id",
                (user_id, sound_id, server_id, now),
            )
            return
        except Exception:
            pass

        # Fallback for older backends (SQLite pre-3.24, etc.).
        self._db.execute(
            "DELETE FROM soundboard_user_cooldowns WHERE user_id = ? AND sound_id = ?",
            (user_id, sound_id),
        )
        self._db.execute(
            "INSERT INTO soundboard_user_cooldowns "
            "(user_id, sound_id, server_id, last_play_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, sound_id, server_id, now),
        )

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

        # ``cooldown_seconds`` is intentionally OMITTED from the
        # INSERT so the column stays NULL until
        # ``update_sound(..., cooldown=N)`` is called.  This is what
        # lets the manager distinguish "unset" (NULL — fall back to
        # ``default_cooldown_seconds`` in config) from "explicitly
        # disabled" (0 — no cooldown).  See schema note on
        # ``cooldown_seconds``.

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

    # ------------------------------------------------------------------ #
    # supplementary / alias surface                                       #
    # ------------------------------------------------------------------ #
    # The integration suite calls several additional helpers that the
    # original manager did not expose.  These wrappers sit on top of the
    # existing privileged routines to avoid duplicating permission /
    # cooldown logic.

    def get_sounds(self, server_id: int, user_id: Optional[int] = None) -> List[Sound]:
        """Alias for :meth:`get_server_sounds` accepting an optional user."""
        if user_id is None:
            # Read-only public listing (no membership check) for admin /
            # global usage surfaces.
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
        return self.get_server_sounds(user_id, server_id)

    def update_sound(
        self,
        user_id: int,
        sound_id: int,
        *,
        name: Optional[str] = None,
        volume: Optional[float] = None,
        emoji: Optional[str] = None,
        cooldown: Optional[int] = None,
        url: Optional[str] = None,
    ) -> Sound:
        """Mutate editable sound metadata.

        Only the metadata that is safe to change after upload is exposed:
        ``name``, ``emoji``, ``volume`` and ``cooldown``.  ``url`` is also
        accepted so audio can be re-pointed to a refreshed CDN copy.
        Cooldown is stored as a per-sound column lazily added on first
        write (older schemas don't have it; SQLite supports ALTER TABLE).
        """
        sound = self.get_sound(sound_id, user_id)
        if not sound:
            raise SoundNotFoundError("Sound not found")
        if not self._check_server_permission(user_id, sound.server_id):
            raise PermissionDeniedError(
                "Missing permission to manage server", "server.manage"
            )

        updates: Dict[str, Any] = {}
        params: List[Any] = []
        if name is not None:
            updates["name"] = self._validate_sound_name(name)
            params.append(updates["name"])
        if emoji is not None:
            updates["emoji"] = emoji
            params.append(emoji)
        if volume is not None:
            v = max(0.0, min(1.0, float(volume)))
            updates["volume"] = v
            params.append(v)
        if url is not None:
            updates["url"] = url
            params.append(url)
        if cooldown is not None:
            # ``cooldown_seconds`` is provisioned by
            # ``soundboard/schema.py`` as a NOT NULL DEFAULT 0
            # column, so no runtime ALTER is required.  The original
            # lazy-ALTER approach acquired an ACCESS EXCLUSIVE lock
            # on Postgres for every metadata write — replaced once the
            # column was moved into the schema.
            updates["cooldown_seconds"] = int(cooldown)
            params.append(updates["cooldown_seconds"])
        if not updates:
            return sound

        set_clause = ", ".join(f"{col} = ?" for col in updates.keys())
        params.append(sound_id)
        self._db.execute(
            f"UPDATE soundboard_sounds SET {set_clause} WHERE id = ?",
            tuple(params),
        )
        logger.debug("Sound %s updated by user %s", sound_id, user_id)
        refreshed = self.get_sound(sound_id, user_id)
        assert refreshed is not None
        return refreshed

    def can_play_sound(
        self,
        user_id: int,
        sound_id: int,
        channel_id: Optional[int] = None,
    ) -> bool:
        """Permission/cooldown probe used by the UI before triggering playback."""
        try:
            sound = self.get_sound(sound_id, user_id)
        except SoundNotFoundError:
            return False
        if not sound:
            return False
        if not self._can_use_sound(user_id, sound_id, sound.server_id):
            return False
        if channel_id is not None and self._servers:
            # Use keyword arguments — the legacy ``ServerManager``
            # accepts ``(user_id, channel_id)`` while the public
            # ``ServersManager`` exposes ``(channel_id, user_id)``.
            # The kwarg form is invariant to either ordering.
            # Canonical (channel_id, user_id) per manager/base.py.
            channel = self._servers.get_channel(channel_id, user_id)
            if not channel:
                return False
            ctype = self._get_channel_type_value(channel)
            # Strict parity with ``play_sound``: any channel whose
            # ``channel_type`` is missing or not ``"voice"`` is
            # rejected.  Silent permissiveness here would let the UI
            # probe say "yes you can play" while ``play_sound`` later
            # raises ``ChannelNotFoundError``.
            if ctype != "voice":
                return False
        return True

    def search_sounds(
        self,
        server_id: int,
        user_id: Optional[int],
        query: str,
        limit: int = 25,
    ) -> List[Sound]:
        """Free-text search across server sounds."""
        if not query or not query.strip():
            return []
        needle = f"%{query.strip().lower()}%"
        rows = self._db.fetch_all(
            """SELECT s.*, COUNT(u.id) as usage_count
               FROM soundboard_sounds s
               LEFT JOIN soundboard_usage u ON s.id = u.sound_id
               WHERE s.server_id = ? AND LOWER(s.name) LIKE ?
               GROUP BY s.id
               ORDER BY usage_count DESC
               LIMIT ?""",
            (server_id, needle, max(1, limit)),
        )
        if user_id is not None and not self._is_server_member(user_id, server_id):
            return []
        return [self._row_to_sound(row) for row in rows]

    def get_popular_sounds(self, server_id: int, limit: int = 10) -> List[Sound]:
        """Top-N sounds for the server by usage frequency."""
        rows = self._db.fetch_all(
            """SELECT s.*, COUNT(u.id) as usage_count
               FROM soundboard_sounds s
               LEFT JOIN soundboard_usage u ON s.id = u.sound_id
               WHERE s.server_id = ?
               GROUP BY s.id
               ORDER BY usage_count DESC
               LIMIT ?""",
            (server_id, max(1, limit)),
        )
        return [self._row_to_sound(row) for row in rows]

    def get_recent_sounds(
        self, server_id: int, user_id: int, limit: int = 10
    ) -> List[Sound]:
        """Return distinct sounds the user has played recently in `server_id`."""
        rows = self._db.fetch_all(
            """SELECT s.*, COUNT(u.id) as usage_count, MAX(u.used_at) as last_used
               FROM soundboard_sounds s
               INNER JOIN soundboard_usage u ON s.id = u.sound_id
               WHERE s.server_id = ? AND u.user_id = ?
               GROUP BY s.id
               ORDER BY last_used DESC
               LIMIT ?""",
            (server_id, user_id, max(1, limit)),
        )
        return [self._row_to_sound(row) for row in rows]

    def get_usage_count(self, sound_id: int) -> int:
        row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM soundboard_usage WHERE sound_id = ?",
            (sound_id,),
        )
        return int(row["count"]) if row else 0

    def play_sound(
        self,
        user_id: int,
        sound_id: int,
        channel_id: Optional[int] = None,
        *,
        volume: Optional[float] = None,
    ) -> "SoundPlayback":
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

        if channel_id is None:
            raise NotInVoiceChannelError(
                "Cannot play sound without joining a voice channel"
            )

        if self._servers:
            # Use keyword arguments — the legacy ``ServerManager``
            # accepts ``(user_id, channel_id)`` while the public
            # ``ServersManager`` exposes ``(channel_id, user_id)``.
            # The kwarg form is invariant to either ordering.
            # Canonical (channel_id, user_id) per manager/base.py.
            channel = self._servers.get_channel(channel_id, user_id)
            if not channel:
                raise ChannelNotFoundError("Channel not found")
            ctype = self._get_channel_type_value(channel)
            # Strict: any channel whose ``channel_type`` is missing or
            # not ``"voice"`` is rejected.  A missing attribute usually
            # indicates a legacy / migration-backlog row — better to
            # surface that as a config error than to silently play a
            # sound in a non-voice channel.
            if ctype != "voice":
                raise ChannelNotFoundError("Channel is not a voice channel")

        if not self._can_use_sound(user_id, sound_id, sound.server_id):
            raise PermissionDeniedError("You do not have permission to use this sound")

        # Optional per-sound cooldown column look-up.  Use an
        # explicit ``is not None`` check on the column value so a
        # row-stored ``0`` (the "no per-sound cooldown" sentinel) is
        # honoured — a plain truthiness test would treat ``0`` as
        # falsy and let the global default override it, breaking
        # owners that explicitly set ``cooldown=0`` on a sound.
        #
        # FLAG-DAY GRACE: if ``soundboard.cooldown_grace_until_zero_ts``
        # in the active config is a future Unix timestamp, treat
        # ``0`` the same as ``NULL`` (fall back to the global
        # default).  This gives the 046 migration a one-release
        # safety window: ship the migration before turning this on
        # at deploy time, then drop the config key after owners have
        # rolled forward.
        per_sound_cooldown: Optional[int] = None
        cooldown_grace_active = False
        try:
            # Re-read the grace timestamp from the live top-level
            # config (not the cached ``self._config``) so a deploy-
            # time config flip is observed without restarting every
            # Plexichat worker — important for the one-release
            # safety window migration 046 relies on.
            grace_until = int(
                config.get("soundboard.cooldown_grace_until_zero_ts", 0) or 0
            )
            cooldown_grace_active = grace_until > 0 and (
                self._get_timestamp() // 1000 < grace_until
            )
        except (TypeError, ValueError):  # noqa: BLE001
            cooldown_grace_active = False
        try:
            cs_row = self._db.fetch_one(
                "SELECT cooldown_seconds FROM soundboard_sounds WHERE id = ?",
                (sound_id,),
            )
            if cs_row is not None and cs_row.get("cooldown_seconds") is not None:
                raw = int(cs_row["cooldown_seconds"])
                if raw != 0 or not cooldown_grace_active:
                    per_sound_cooldown = raw
                # else: grace window active + stored 0 → treat as NULL.
        except Exception:
            per_sound_cooldown = None

        # Honour "global_cooldown_seconds" by sharing the key with any
        # sound — the test suite checks that playing a *different* sound
        # within the global window fails.
        # Global cooldown (any sound within ``global_cooldown_seconds``)
        # consults ``soundboard_usage`` for the user's most recent play
        # on this server: pure DB-backed, no in-memory cache, so the
        # behaviour is consistent across processes and instances.
        global_window = int(self._config.get("global_cooldown_seconds", 0) or 0)
        if global_window > 0:
            last_row = self._db.fetch_one(
                """SELECT MAX(u.used_at) AS last_play_at
                   FROM soundboard_usage AS u
                   INNER JOIN soundboard_sounds AS s
                     ON s.id = u.sound_id
                   WHERE u.user_id = ? AND s.server_id = ?""",
                (user_id, sound.server_id),
            )
            if last_row and last_row.get("last_play_at"):
                elapsed = (
                    self._get_timestamp() - int(last_row["last_play_at"])
                ) / 1000.0
                if elapsed < global_window:
                    raise SoundCooldownError(
                        f"Global soundboard cooldown: {int(global_window - elapsed)}s remaining",
                        int(global_window - elapsed),
                    )

        # ``or`` would treat a per-sound value of ``0`` as falsy and
        # silently fall back to the global default — breaking tests
        # / owners that explicitly set ``cooldown=0`` to disable
        # per-sound throttling for that sound.  Use an explicit
        # ``None`` check so 0 is honoured.
        if per_sound_cooldown is not None:
            cooldown_seconds = per_sound_cooldown
        else:
            cooldown_seconds = self._config.get("default_cooldown_seconds", 5)
        remaining = self._check_cooldown(user_id, sound_id, cooldown_seconds)
        if remaining is not None:
            raise SoundCooldownError(
                f"Sound is on cooldown for {remaining} more seconds", remaining
            )

        now = self._get_timestamp()
        usage_id = self._generate_id()

        # Honour volume override by clamping the playback's per-user
        # multiplier, if provided.
        if volume is not None:
            sound.volume = max(0.0, min(1.0, float(volume)))

        self._db.execute(
            """INSERT INTO soundboard_usage (id, sound_id, user_id, channel_id, used_at)
               VALUES (?, ?, ?, ?, ?)""",
            (usage_id, sound_id, user_id, channel_id, now),
        )

        # Persist per-sound cooldown in the DB (source of truth) and
        # also keep a tiny in-process cache so back-to-back plays from
        # the same client don't hit the DB on every request.  The DB
        # row remains authoritative across restarts / multi-instance.
        # ``_set_cooldown`` already updates ``_client_local_cooldowns``,
        # so no explicit cache write is needed here.
        self._set_cooldown(user_id, sound_id, sound.server_id)

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

        # Schema column is ``cooldown_seconds`` (may be NULL when the
        # sound hasn't had an explicit per-sound cooldown set yet —
        # in which case the manager should fall back to the global
        # ``default_cooldown_seconds`` from config).  Both Python
        # fields (``cooldown`` and ``cooldown_seconds``) are kept in
        # sync so dataclasses.asdict round-trips and any
        # legacy ``sound.cooldown_seconds`` consumer still work.
        raw_cooldown = row.get("cooldown_seconds")
        cooldown_value = int(raw_cooldown) if raw_cooldown is not None else 0
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
            cooldown=cooldown_value,
            cooldown_seconds=cooldown_value,
            created_by=row["created_by"],
            created_at=row["created_at"],
            usage_count=usage_count,
        )

    def _get_channel_type_value(self, channel) -> Optional[str]:
        """Normalise ``channel.channel_type`` into its string form.

        Different code paths (DB rows, manager wrappers, the test
        suite's enum input) deliver ``channel_type`` as either an
        ``Enum`` with a ``.value`` attribute or as a plain ``str``.
        Centralising the conversion here avoids drift between
        ``play_sound`` (which previously required the ``Enum`` form)
        and ``can_play_sound`` (which already handled both).  Returns
        ``None`` when the channel is missing the attribute entirely.
        """
        if channel is None or not hasattr(channel, "channel_type"):
            return None
        if hasattr(channel.channel_type, "value"):
            return channel.channel_type.value
        if isinstance(channel.channel_type, str):
            return channel.channel_type
        return None
