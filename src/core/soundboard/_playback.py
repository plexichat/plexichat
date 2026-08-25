"""
Soundboard playback mixin - cooldown enforcement and playback operations.

Split out of ``manager.py`` to keep each module focused and below the
repository's 1000-line file ceiling. ``SoundboardManager`` inherits from
:class:`SoundboardPlaybackMixin`; the public import path
(``from src.core.soundboard.manager import SoundboardManager``) is unchanged.
"""

from typing import Any, Callable, List, Optional

import utils.config as config
import utils.logger as logger

from .models import Sound, SoundPlayback
from .exceptions import (
    ChannelNotFoundError,
    NotInVoiceChannelError,
    PermissionDeniedError,
    SoundCooldownError,
    SoundNotFoundError,
)


# Attributes/methods provided by the composing SoundboardManager. Declared as
# a real (empty) base class so static checkers see the contract on the mixin;
# it contributes no runtime behaviour of its own.
class _PlaybackContract:
    _db: Any
    _config: Any
    _servers: Optional[Any]

    def _get_timestamp(self) -> int:
        raise NotImplementedError

    def _generate_id(self) -> int:
        raise NotImplementedError

    def get_sound(self, sound_id: int, user_id: int) -> Optional[Sound]:
        raise NotImplementedError

    def get_server_sounds(self, user_id: int, server_id: int) -> List[Sound]:
        raise NotImplementedError

    def _is_server_member(self, user_id: int, server_id: int) -> bool:
        raise NotImplementedError

    def _can_use_sound(self, user_id: int, sound_id: int, server_id: int) -> bool:
        raise NotImplementedError

    def _row_to_sound(self, row) -> Sound:
        raise NotImplementedError


class SoundboardPlaybackMixin(_PlaybackContract):
    """Cooldown + playback behaviour for :class:`SoundboardManager`.

    Relies on attributes provided by the manager: ``_db``, ``_config``,
    ``_servers``, ``_get_timestamp``, ``_generate_id`` and the shared row
    helpers (``get_sound``, ``_row_to_sound``, ``_is_server_member``).
    """

    # ------------------------------------------------------------------ #
    # cooldown state                                                      #
    # ------------------------------------------------------------------ #
    # Cooldown state persists in the ``soundboard_user_cooldowns`` table
    # (created by ``soundboard/schema.py``); we deliberately do NOT keep an
    # authoritative in-memory cache — process restarts and multi-instance
    # deployments must see the same enforcement. The tiny per-process cache
    # below only avoids a DB round-trip on repeat plays from one connection.

    def _check_cooldown(
        self, user_id: int, sound_id: int, cooldown_seconds: int
    ) -> Optional[int]:
        """
        Check if sound is on cooldown for ``user_id``.

        Source of truth is the ``soundboard_user_cooldowns`` table so
        enforcement survives process restarts and works across multiple
        Plexichat instances; the in-process cache is consulted first to avoid
        a DB round-trip on every repeat play.  Returns ``None`` if the user
        is free to play the sound again, otherwise the remaining seconds.
        """
        if cooldown_seconds <= 0:
            return None

        now = self._get_timestamp()
        candidates: List[int] = []

        local_ts = getattr(self, "_client_local_cooldowns", {}).get((user_id, sound_id))
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
        except Exception as e:
            logger.error(
                f"Failed to check cooldown for user {user_id} sound {sound_id}: {e}"
            )
            return cooldown_seconds

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
        the DB.
        """
        now = self._get_timestamp()
        cache = getattr(self, "_client_local_cooldowns", None)
        if cache is not None:
            cache[(user_id, sound_id)] = now

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
        except Exception as e:
            logger.error(
                f"Failed to persist cooldown for user {user_id} sound {sound_id}: {e}"
            )
            raise

    def _run_in_transaction(self, operation: Callable[[], Any]) -> Any:
        """Run ``operation`` inside an explicit DB transaction."""
        self._db.begin_transaction()
        try:
            result = operation()
        except Exception:
            self._db.rollback()
            raise
        self._db.commit()
        return result

    def _acquire_cooldown(
        self, user_id: int, sound_id: int, server_id: int, cooldown_seconds: int
    ) -> Optional[int]:
        """Atomically claim the per-user cooldown slot for ``sound_id``.

        The historical check-then-set pair (``_check_cooldown`` followed by
        ``_set_cooldown``) let concurrent plays race past the gate. Here a
        single conditional UPDATE inside a transaction performs the
        check-and-set:

        * ``UPDATE ... SET last_play_at = now WHERE last_play_at <= cutoff``
          succeeds only when the previous window has expired — its rowcount
          decides ownership atomically.
        * When no row exists yet, an INSERT is attempted; the table's
          ``PRIMARY KEY (user_id, sound_id)`` makes a lost concurrent insert
          fail loudly instead of silently double-claiming.

        Returns ``None`` when the slot was acquired (caller may record the
        play), otherwise the remaining cooldown seconds.
        """
        if cooldown_seconds <= 0:
            return None

        now = self._get_timestamp()
        cutoff = now - int(cooldown_seconds * 1000)

        def op() -> Optional[int]:
            cursor = self._db.execute(
                "UPDATE soundboard_user_cooldowns SET last_play_at = ? "
                "WHERE user_id = ? AND sound_id = ? AND last_play_at <= ?",
                (now, user_id, sound_id, cutoff),
            )
            if getattr(cursor, "rowcount", 0) and cursor.rowcount > 0:
                return None

            row = self._db.fetch_one(
                "SELECT last_play_at FROM soundboard_user_cooldowns "
                "WHERE user_id = ? AND sound_id = ?",
                (user_id, sound_id),
            )
            if row and row.get("last_play_at") is not None:
                last_used = int(row["last_play_at"])
                if last_used > cutoff:
                    elapsed = (now - last_used) / 1000.0
                    return max(1, int(cooldown_seconds - elapsed))

            try:
                self._db.execute(
                    "INSERT INTO soundboard_user_cooldowns "
                    "(user_id, sound_id, server_id, last_play_at) "
                    "VALUES (?, ?, ?, ?)",
                    (user_id, sound_id, server_id, now),
                )
            except Exception as e:
                # Lost an INSERT race against a concurrent play — treat as
                # contention rather than failing the request with a 500.
                logger.debug(
                    f"Cooldown insert raced for user {user_id} sound {sound_id}: {e}"
                )
                return max(1, int(cooldown_seconds))
            return None

        return self._run_in_transaction(op)

    # ------------------------------------------------------------------ #
    # playback                                                            #
    # ------------------------------------------------------------------ #

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
            channel = self._servers.get_channel(channel_id, user_id)
            if not channel:
                return False
            ctype = self._get_channel_type_value(channel)
            # Strict parity with ``play_sound``: any channel whose
            # ``channel_type`` is missing or not ``"voice"`` is rejected.
            if ctype != "voice":
                return False
        return True

    def play_sound(
        self,
        user_id: int,
        sound_id: int,
        channel_id: Optional[int] = None,
        *,
        volume: Optional[float] = None,
    ) -> SoundPlayback:
        """
        Play a sound in a voice channel.

        This triggers a playback event that the voice module should handle.

        Args:
            user_id: ID of user playing sound
            sound_id: ID of sound
            channel_id: ID of voice channel
            volume: Optional per-playback volume override (does not persist)

        Returns:
            SoundPlayback event

        Raises:
            SoundNotFoundError: Sound not found (or caller is not a member of
                the sound's server)
            ChannelNotFoundError: Channel missing, not voice, or in a
                different server than the sound
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

        channel_server_id = None
        if self._servers:
            channel = self._servers.get_channel(channel_id, user_id)
            if not channel:
                raise ChannelNotFoundError("Channel not found")
            ctype = self._get_channel_type_value(channel)
            # Strict: any channel whose ``channel_type`` is missing or
            # not ``"voice"`` is rejected. A missing attribute usually
            # indicates a legacy / migration-backlog row — better to
            # surface that as a config error than to silently play a
            # sound in a non-voice channel.
            if ctype != "voice":
                raise ChannelNotFoundError("Channel is not a voice channel")
            # Cross-server guard: playing a sound into another server's
            # voice channel must be blocked even when the caller is a
            # member of both servers — channel access alone used to be
            # the only check.
            channel_server_id = getattr(channel, "server_id", None)
            if channel_server_id is not None and int(channel_server_id) != int(
                sound.server_id
            ):
                raise ChannelNotFoundError(
                    "Channel belongs to a different server than the sound"
                )

        if not self._can_use_sound(user_id, sound_id, sound.server_id):
            raise PermissionDeniedError("You do not have permission to use this sound")

        # Optional per-sound cooldown column look-up. Use an explicit
        # ``is not None`` check on the column value so a row-stored ``0``
        # (the "no per-sound cooldown" sentinel) is honoured — a plain
        # truthiness test would treat ``0`` as falsy and let the global
        # default override it, breaking owners that explicitly set
        # ``cooldown=0`` on a sound.
        #
        # FLAG-DAY GRACE: if ``soundboard.cooldown_grace_until_zero_ts``
        # in the active config is a future Unix timestamp, treat ``0`` the
        # same as ``NULL`` (fall back to the global default). This gives
        # the 046 migration a one-release safety window.
        per_sound_cooldown: Optional[int] = None
        cooldown_grace_active = False
        try:
            grace_until = int(
                config.get("soundboard.cooldown_grace_until_zero_ts", 0) or 0
            )
            cooldown_grace_active = grace_until > 0 and (
                self._get_timestamp() // 1000 < grace_until
            )
        except (TypeError, ValueError):
            cooldown_grace_active = False
        cs_row = self._db.fetch_one(
            "SELECT cooldown_seconds FROM soundboard_sounds WHERE id = ?",
            (sound_id,),
        )
        if cs_row is not None and cs_row.get("cooldown_seconds") is not None:
            raw = int(cs_row["cooldown_seconds"])
            if raw != 0 or not cooldown_grace_active:
                per_sound_cooldown = raw

        # Global cooldown (any sound within ``global_cooldown_seconds``)
        # consults ``soundboard_usage`` for the user's most recent play on
        # this server: pure DB-backed, no in-memory cache.
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
        # silently fall back to the global default — breaking owners that
        # explicitly set ``cooldown=0`` to disable per-sound throttling.
        if per_sound_cooldown is not None:
            cooldown_seconds = per_sound_cooldown
        else:
            cooldown_seconds = self._config.get("default_cooldown_seconds", 3)

        # Atomically claim the cooldown slot BEFORE recording usage so two
        # concurrent plays cannot both pass the check-then-set window.
        remaining = self._acquire_cooldown(
            user_id, sound_id, sound.server_id, int(cooldown_seconds)
        )
        if remaining is not None:
            raise SoundCooldownError(
                f"Sound is on cooldown for {remaining} more seconds", remaining
            )
        cache = getattr(self, "_client_local_cooldowns", None)
        if cache is not None:
            cache[(user_id, sound_id)] = self._get_timestamp()

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

        logger.debug(
            f"Sound {sound_id} played by user {user_id} in channel {channel_id}"
        )

        return SoundPlayback(
            sound=sound, user_id=user_id, channel_id=channel_id, timestamp=now
        )

    # ------------------------------------------------------------------ #
    # supplementary listing / query surface                               #
    # ------------------------------------------------------------------ #

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

    def search_sounds(
        self,
        server_id: int,
        user_id: Optional[int],
        query: str,
        limit: int = 25,
    ) -> List[Sound]:
        """Free-text search across server sounds (membership checked first)."""
        if user_id is not None and not self._is_server_member(user_id, server_id):
            return []
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

    def _get_channel_type_value(self, channel) -> Optional[str]:
        """Normalise ``channel.channel_type`` into its string form.

        Different code paths (DB rows, manager wrappers, the test suite's
        enum input) deliver ``channel_type`` as either an ``Enum`` with a
        ``.value`` attribute or as a plain ``str``. Returns ``None`` when
        the channel is missing the attribute entirely.
        """
        if channel is None or not hasattr(channel, "channel_type"):
            return None
        if hasattr(channel.channel_type, "value"):
            return channel.channel_type.value
        if isinstance(channel.channel_type, str):
            return channel.channel_type
        return None
