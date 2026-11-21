"""
Soundboard manager - Core business logic for soundboard operations.

Handles sound upload, permissions, cooldowns, and playback triggering
with proper validation and permission checks.
"""

import math
import re

from typing import Optional, List, Dict, Any

import utils.config as config
import utils.logger as logger
from src.core.avatars.manager.upload_guard import (
    UploadContentError,
    normalize_mime,
    sanitize_upload_filename,
    verify_upload_content,
)
from src.core.avatars.manager.url_guard import is_safe_media_url
from src.core.base import BaseManager
from src.core.bounded_lru import BoundedLRU
from src.core.security.hardening import clamp_finite_float

from .models import (
    Sound,
    SoundPermissions,
    SoundFormat,
    SOUNDBOARD_MANAGE_PERMISSION,
)
from ._playback import SoundboardPlaybackMixin
from .exceptions import (
    SoundNotFoundError,
    SoundLimitError,
    InvalidSoundFormatError,
    InvalidSoundUrlError,
    SoundTooLargeError,
    SoundTooLongError,
    InvalidSoundNameError,
    PermissionDeniedError,
    ServerNotFoundError,
)


# A playback (and the UI's ``can_play_sound`` probe) re-checks every sound's
# allow/deny list and the caller's role set on every single call. Both inputs
# change only on rare admin actions, so a short-lived memo collapses a burst of
# plays/probes into one authoritative read without weakening the gate:
# * the key carries the FULL (user, sound, server) identity, so one user's
#   verdict can never be served for another (no cross-user leak);
# * the TTL is short and local mutations clear it eagerly, bounding how long a
#   permission/role change made on another worker can stay stale (multi-node
#   safe);
# * the store is a bounded LRU, so a flood of distinct ids stays finite;
# * a miss always recomputes from the authoritative database, and a DENY is
#   cached exactly like an ALLOW, so the memo never widens access on error.
_SOUND_USE_CACHE_TTL_SECONDS = 2.0
_SOUND_USE_CACHE_MAX_ENTRIES = 512
_SOUND_USE_CACHE_MAX_BYTES = 64 * 1024

_CACHE_MISS = object()


# One statement re-checks the per-server sound cap at the instant of the
# insert. The old flow ran a separate ``SELECT COUNT(*)`` quota check and only
# then INSERTed; two concurrent uploads could both read a below-cap count and
# both insert, pushing a server over ``max_sounds_per_server`` (a TOCTOU quota
# bypass). The guard subquery sees the table state *before* the new row exists,
# so exactly the callers that still fit get a row and ``rowcount == 0`` is an
# authoritative "limit reached" signal. SQLite serialises this single statement
# with its write lock; PostgreSQL's READ COMMITTED snapshot would let two
# statements both pass, so upload_sound() additionally takes an advisory xact
# lock (see there).
_ATOMIC_SOUND_INSERT_SQL = """
INSERT INTO soundboard_sounds
    (id, server_id, name, format, emoji, url, size, duration_seconds, volume, created_by, created_at)
SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
WHERE (SELECT COUNT(*) FROM soundboard_sounds WHERE server_id = ?) < ?
"""


class SoundboardManager(BaseManager, SoundboardPlaybackMixin):
    """Core soundboard manager handling all operations."""

    def __init__(
        self,
        db,
        auth_module=None,
        servers_module=None,
        media_module=None,
    ):
        """
        Initialize the soundboard manager.

        Args:
            db: Database instance (must be connected)
            auth_module: Optional auth module for user verification
            servers_module: Optional servers module for permission checks
            media_module: Optional media pipeline for byte uploads
        """
        super().__init__(db, auth_module)
        self._servers = servers_module
        self._media = media_module
        self._config = self._load_config()
        # Cooldown state persists in the ``soundboard_user_cooldowns``
        # table (created by ``soundboard/schema.py``); we deliberately
        # do NOT keep an in-memory cache — process restarts and
        # multi-instance deployments must see the same enforcement.
        self._client_local_cooldowns: Dict[tuple, int] = {}

        # Bounded, short-TTL memo of sound-usage authorization decisions
        # (see the module constants above). Instance-local so each manager has
        # its own store; cleared on every local permission/role-affecting write.
        self._sound_use_cache = BoundedLRU(
            max_entries=_SOUND_USE_CACHE_MAX_ENTRIES,
            max_bytes=_SOUND_USE_CACHE_MAX_BYTES,
            ttl_seconds=_SOUND_USE_CACHE_TTL_SECONDS,
        )

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
        """Check if user has manage-server (or soundboard) permission.

        SECURITY: fails CLOSED — without the servers module the check
        cannot be evaluated, so deny instead of granting everything.
        """
        if not self._servers:
            logger.error(
                "Soundboard permission check failed CLOSED: servers module "
                "is not wired into the soundboard manager."
            )
            return False
        return self._servers.has_permission(
            user_id, server_id, SOUNDBOARD_MANAGE_PERMISSION
        ) or self._servers.has_permission(user_id, server_id, "server.manage")

    def _is_server_member(self, user_id: int, server_id: int) -> bool:
        """Check if user is a member of the server (fails closed)."""
        if not self._servers:
            logger.error(
                "Soundboard membership check failed CLOSED: servers module "
                "is not wired into the soundboard manager."
            )
            return False
        member = self._servers.get_member(server_id, user_id)
        return member is not None

    def _get_user_roles(self, user_id: int, server_id: int) -> List[int]:
        """Get user's role IDs in a server."""
        if not self._servers:
            return []
        roles = self._servers.get_member_roles(server_id, user_id)
        return [role.id for role in roles]

    # ------------------------------------------------------------------ #
    # format + URL validation                                             #
    # ------------------------------------------------------------------ #

    _AUDIO_EXT_ALIASES = {
        "mp3": "mp3",
        "ogg": "ogg",
        "oga": "ogg",
        "wav": "wav",
        "webm": "webm",
    }

    _AUDIO_MIME_ALIASES = {
        "mpeg": "mp3",
        "mp3": "mp3",
        "ogg": "ogg",
        "vorbis": "ogg",
        "wav": "wav",
        "x-wav": "wav",
        "wave": "wav",
        "vnd.wave": "wav",
        "webm": "webm",
    }

    @classmethod
    def _normalize_audio_format(cls, value: Any) -> Optional[str]:
        """Normalise an extension or MIME form to a canonical extension."""
        if not isinstance(value, str):
            return None
        v = value.strip().lower()
        if v.startswith("audio/"):
            return cls._AUDIO_MIME_ALIASES.get(v[len("audio/") :])
        if v.startswith("."):
            v = v[1:]
        if "/" in v:
            v = v.rsplit("/", 1)[-1]
        return cls._AUDIO_EXT_ALIASES.get(v)

    def _format_allowed(self, fmt: Any) -> bool:
        """Check ``fmt`` (extension or MIME) against the configured allow-list.

        Both sides are normalised so ``"audio/mpeg"``, ``".mp3"`` and
        ``"mp3"`` are equivalent regardless of which form the config or
        the client used.
        """
        canonical = self._normalize_audio_format(fmt)
        if canonical is None:
            return False
        for entry in self._config.get("allowed_formats", ["mp3", "ogg"]):
            if self._normalize_audio_format(entry) == canonical:
                return True
        return False

    def _validate_sound_url(self, url: str) -> str:
        """Validate a sound URL, rejecting dangerous schemes.

        Only absolute ``http(s)`` URLs and server-relative media paths
        (``/...``) are accepted; ``javascript:``, ``data:``, ``file:`` and
        friends are rejected as a stored-XSS / SSRF defence.

        The check is performed on a browser-normalised copy of the value:
        HTML character references (``javascript&#58;``) and WHATWG control
        character stripping (``java\\nscript:``) can turn an otherwise
        innocuous-looking string into a script-bearing scheme once it is
        rendered into an ``<audio src>`` attribute. Matching the raw string
        let those payloads through.
        Returns the trimmed, original URL.
        """
        if not isinstance(url, str) or not url.strip():
            raise InvalidSoundUrlError("Sound URL must be a non-empty string")
        url = url.strip()
        if not is_safe_media_url(url):
            raise InvalidSoundUrlError("Sound URL scheme is not allowed")
        return url

    _MP3_FRAME_SYNC = 0xE0

    @staticmethod
    def _detect_audio_format(data: bytes) -> Optional[str]:
        """Detect the audio container of raw bytes via magic numbers.

        Returns the canonical extension (``mp3``, ``ogg``, ``wav``,
        ``webm``) or ``None`` when the bytes do not match any known
        audio container — client-declared Content-Type is never trusted.
        """
        if not data:
            return None
        if data[:3] == b"ID3":
            return "mp3"
        if (
            len(data) >= 2
            and data[0] == 0xFF
            and (data[1] & SoundboardManager._MP3_FRAME_SYNC)
            == SoundboardManager._MP3_FRAME_SYNC
        ):
            return "mp3"
        if data[:4] == b"OggS":
            return "ogg"
        if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
            return "wav"
        if data[:4] == b"\x1aE\xdf\xa3":
            return "webm"
        return None

    def create_sound_from_upload(
        self,
        user_id: int,
        server_id: int,
        name: str,
        file_data: bytes,
        filename: Optional[str] = None,
        content_type: Optional[str] = None,
        duration_seconds: Optional[float] = None,
        emoji: Optional[str] = None,
        volume: float = 1.0,
    ) -> Sound:
        """Store uploaded audio bytes via the shared media pipeline.

        The real container format is derived from magic bytes (never the
        client-declared Content-Type), checked against the configured
        allow-list, then persisted through :meth:`upload_sound` with the
        pipeline-produced URL.
        """
        detected = self._detect_audio_format(file_data)
        allowed = self._config.get("allowed_formats", ["mp3", "ogg"])
        if detected is None:
            raise InvalidSoundFormatError(
                "Uploaded bytes are not a recognised audio container",
                content_type or (filename or "").rsplit(".", 1)[-1],
                allowed,
            )
        if not self._format_allowed(detected):
            raise InvalidSoundFormatError(
                f"Format {detected} not allowed", detected, allowed
            )

        # DEEP: pin verification to the format derived from the magic bytes.
        # A client declaring a non-audio type (or smuggling markup after a
        # valid audio header) is rejected even though the header matches.
        declared = normalize_mime(content_type)
        if (
            declared
            and declared != "application/octet-stream"
            and not declared.startswith("audio/")
        ):
            raise InvalidSoundFormatError(
                "Declared content type is not audio", declared, allowed
            )
        try:
            verify_upload_content(
                content_type,
                file_data,
                expected_mime=f"audio/{detected}",
                allow_unspecified=True,
            )
        except UploadContentError as exc:
            raise InvalidSoundFormatError(
                "Uploaded audio bytes failed content verification", detected, allowed
            ) from exc

        media = getattr(self, "_media", None)
        if media is None or not hasattr(media, "upload_file"):
            raise RuntimeError("Media storage unavailable")

        max_size = int(
            self._config.get(
                "max_sound_size", config.get("soundboard.max_sound_size", 524288)
            )
            or 524288
        )
        if len(file_data) > max_size:
            raise SoundTooLargeError(
                f"Sound exceeds maximum size of {max_size} bytes",
                max_size,
                len(file_data),
            )

        ext_suffix = (filename or f"sound.{detected}").rsplit(".", 1)[-1].lower()
        safe_ext = (
            ext_suffix
            if self._normalize_audio_format(ext_suffix) == detected
            else detected
        )
        # DEEP: confine the generated filename to a single safe component so a
        # crafted name/filename can never influence a storage path.
        upload_filename = (
            f"{sanitize_upload_filename(name, default='sound')}."
            f"{sanitize_upload_filename(safe_ext, default=detected)}"
        )
        upload_ct = "audio/mpeg" if detected == "mp3" else f"audio/{detected}"

        result = media.upload_file(user_id, file_data, upload_filename, upload_ct)
        try:
            return self.upload_sound(
                user_id=user_id,
                server_id=server_id,
                name=name,
                format=SoundFormat(detected),
                url=result.url,
                size=len(file_data),
                duration_seconds=float(duration_seconds or 0.0),
                emoji=emoji,
                volume=volume,
            )
        except Exception:
            # Roll back the just-uploaded file so storage does not
            # accumulate orphaned media objects when validation fails
            # after the bytes were already written.
            try:
                if hasattr(media, "delete_file_permanently"):
                    media.delete_file_permanently(user_id, int(result.file_id))
                elif hasattr(media, "delete_file"):
                    media.delete_file(user_id, int(result.file_id))
            except Exception as cleanup_err:
                logger.warning(
                    "Failed to clean up orphaned soundboard media %s: %s",
                    getattr(result, "file_id", "?"),
                    cleanup_err,
                )
            raise

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

        # A permitted upload targeting a vanished server must not create
        # orphaned rows — verify the backing server still exists.
        if (
            self._db.fetch_one(
                "SELECT 1 FROM srv_servers WHERE id = ? AND deleted = 0",
                (server_id,),
            )
            is None
        ):
            raise ServerNotFoundError(f"Server {server_id} not found")

        name = self._validate_sound_name(name)
        url = self._validate_sound_url(url)

        if not self._format_allowed(format.value):
            allowed_formats = self._config.get("allowed_formats", ["mp3", "ogg"])
            raise InvalidSoundFormatError(
                f"Format {format.value} not allowed", format.value, allowed_formats
            )

        max_size = self._config.get("max_sound_size", 524288)
        if size > max_size:
            raise SoundTooLargeError(
                f"Sound exceeds maximum size of {max_size} bytes", max_size, size
            )

        max_duration = self._config.get("max_sound_duration_seconds", 5)
        # NaN compares False against every bound, so the naive check let a
        # non-finite duration through into storage. A stored NaN then makes
        # every JSON response for this server's sounds unserializable
        # (allow_nan=False) -- a persistent listing outage. Reject it here.
        if not math.isfinite(duration_seconds) or duration_seconds > max_duration:
            raise SoundTooLongError(
                f"Sound exceeds maximum duration of {max_duration} seconds",
                max_duration,
                duration_seconds,
            )

        # clamp_finite_float also covers NaN/inf, which the old
        # ``volume < 0.0 or volume > 1.0`` guard let through unchanged.
        volume = clamp_finite_float(volume, 0.0, 1.0, default=0.0)

        max_sounds = self._config.get("max_sounds_per_server", 100)

        # Early, advisory quota feedback (avoids a wasted insert). It is NOT the
        # authoritative check: two concurrent creators can both read a
        # below-cap count, so the atomic guarded INSERT below is what actually
        # enforces the cap.
        count_row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM soundboard_sounds WHERE server_id = ?",
            (server_id,),
        )
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

        is_postgres = getattr(self._db, "type", "") == "postgres"
        if is_postgres:
            # PostgreSQL READ COMMITTED would let two concurrent INSERT..SELECT
            # statements both see a stale count. Serialise per server with an
            # advisory xact lock. Namespace 120 stays clear of the invite (1/2)
            # and emoji (100/101) lock namespaces; masking to 30 bits keeps the
            # key in signed int4 range.
            self._db.begin_transaction()
            try:
                self._db.execute(
                    "SELECT pg_advisory_xact_lock(?, ?)",
                    (120, int(server_id) & 0x3FFFFFFF),
                )
            except Exception:
                self._db.rollback()
                raise
        try:
            if not is_postgres:
                self._db.begin_transaction()

            cursor = self._db.execute(
                _ATOMIC_SOUND_INSERT_SQL,
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
                    server_id,
                    max_sounds,
                ),
            )
            # rowcount 0 means the count guard found the server already at (or
            # over) its cap at insert time — a concurrent creator lost the
            # race. Roll back and surface the same quota error the advisory
            # check would have raised.
            if getattr(cursor, "rowcount", 0) == 0:
                raise SoundLimitError(
                    f"Server has reached maximum of {max_sounds} sounds",
                    max_sounds,
                    max_sounds,
                )
            self._db.commit()
        except SoundLimitError:
            self._db.rollback()
            raise
        except Exception:
            self._db.rollback()
            raise

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
        # The sound's allow/deny list is gone; drop any memoized verdicts so a
        # later call recomputes from the authoritative (now empty) rows.
        self._sound_use_cache.clear()

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

        # Cross-server guard: a role from a DIFFERENT server must never be
        # attachable to this sound's permission rows.
        role_row = self._db.fetch_one(
            "SELECT server_id FROM srv_roles WHERE id = ? AND deleted = 0",
            (role_id,),
        )
        if not role_row or int(role_row["server_id"]) != int(sound.server_id):
            raise PermissionDeniedError("Role does not belong to this sound's server")

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

        # A grant/deny just changed for this sound; invalidate every memoized
        # verdict so the next play/probe sees the new allow-list immediately.
        self._sound_use_cache.clear()

        return SoundPermissions(
            id=perm_id, sound_id=sound_id, role_id=role_id, can_use=can_use
        )

    def _can_use_sound(self, user_id: int, sound_id: int, server_id: int) -> bool:
        """Check if user can use a sound based on role permissions.

        Semantics: with no permission rows configured every member may
        play. Once rows exist they act as an ALLOW-list (deny-wins): the
        user needs at least one matching ``can_use`` entry and no
        matching deny entry — members whose roles match nothing are NOT
        implicitly allowed.

        The decision is memoized for a short window (see the module constants).
        The key includes ``user_id`` so one caller's verdict can never be
        reused for another, and a sentinel distinguishes a cached ``False``
        from a miss. Local permission mutations clear the store eagerly; the
        short TTL bounds staleness from changes on other workers.
        """
        cache = self._sound_use_cache
        key = (user_id, sound_id, server_id)
        cached = cache.get(key, _CACHE_MISS)
        if cached is not _CACHE_MISS:
            return cached
        allowed = self._compute_can_use_sound(user_id, sound_id, server_id)
        cache.set(key, allowed)
        return allowed

    def _compute_can_use_sound(
        self, user_id: int, sound_id: int, server_id: int
    ) -> bool:
        """Authoritative (uncached) sound-usage decision — DB reads live."""
        perms = self._db.fetch_all(
            "SELECT role_id, can_use FROM soundboard_permissions WHERE sound_id = ?",
            (sound_id,),
        )

        if not perms:
            return True

        user_roles = set(self._get_user_roles(user_id, server_id))
        denied = any(
            not perm["can_use"] and perm["role_id"] in user_roles for perm in perms
        )
        if denied:
            return False
        return any(perm["can_use"] and perm["role_id"] in user_roles for perm in perms)

    # ------------------------------------------------------------------ #
    # supplementary / alias surface                                       #
    # ------------------------------------------------------------------ #
    # The integration suite calls several additional helpers that the
    # original manager did not expose.  These wrappers sit on top of the
    # existing privileged routines to avoid duplicating permission /
    # cooldown logic.

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
        ``cooldown_seconds`` ships in the schema, so a write here needs no
        runtime ALTER on either backend.
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
            # Fail safe on NaN/inf: the old max/min idiom silently stored NaN
            # as full volume (1.0) and let a non-finite value reach clients.
            v = clamp_finite_float(volume, 0.0, 1.0, default=0.0)
            updates["volume"] = v
            params.append(v)
        if url is not None:
            updates["url"] = self._validate_sound_url(url)
            params.append(updates["url"])
        if cooldown is not None:
            # ``cooldown_seconds`` is provisioned by ``soundboard/schema.py``
            # as a plain nullable INTEGER (no DEFAULT), and
            # ``upload_sound`` deliberately leaves it NULL so the column can
            # distinguish "unset" (NULL — fall back to
            # ``default_cooldown_seconds`` in config) from "explicitly
            # disabled" (0 — no cooldown). Writing an explicit value here is
            # what ends that ambiguity, so the previously documented NOT NULL
            # DEFAULT 0 shape would have made every sound read as
            # "cooldown disabled" and silently dropped the configured default.
            clamped = int(cooldown)
            max_cooldown = int(self._config.get("max_cooldown_seconds", 0) or 0)
            if max_cooldown > 0:
                clamped = min(clamped, max_cooldown)
            updates["cooldown_seconds"] = clamped
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
