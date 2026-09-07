"""
Sticker manager - Core business logic for sticker operations.
"""

import json
import re
from typing import Optional, List, Dict, Any

import utils.config as config
import utils.logger as logger
from src.core.base import timestamp_ms, generate_id

from ._discovery import StickerUsageDiscoveryMixin
from .models import (
    StickerPack,
    Sticker,
    StickerFormat,
    PackType,
)
from .exceptions import (
    PackNotFoundError,
    StickerNotFoundError,
    PackLimitError,
    StickerLimitError,
    InvalidStickerFormatError,
    StickerTooLargeError,
    InvalidStickerNameError,
    InvalidPackNameError,
    PermissionDeniedError,
    ServerNotFoundError,
)


class StickerManager(StickerUsageDiscoveryMixin):
    def __init__(
        self, db, messaging_module=None, servers_module=None, media_module=None
    ):
        self._db = db
        self._messaging = messaging_module
        self._servers = servers_module
        self._media = media_module
        self._config = self._load_config()
        self._encrypt_descriptions = config.get(
            "encryption.encrypt_descriptions", False
        )
        logger.info("Sticker module initialized")

    def _load_config(self) -> Dict[str, Any]:
        """Load sticker configuration from global config."""
        return config.get("stickers", {})

    _STICKER_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

    @staticmethod
    def _detect_sticker_format(data: bytes) -> Optional[str]:
        """Detect the on-disk format of sticker bytes from magic bytes.

        Returns one of ``"png"`` (also covers APNG), ``"json"`` (Lottie), or
        ``None`` for anything unsupported or unsafe (SVG, HTML, plain text,
        garbage). The PNG magic is required: a ``image/png`` Content-Type whose
        bytes are actually SVG/HTML must be rejected, since a ``*.png`` sticker
        that is really an SVG/HTML document is a stored-XSS vector.
        """
        if not data:
            return None
        stripped = data.lstrip()
        if stripped[:1] in (b"{", b"["):
            try:
                json.loads(data.decode("utf-8", "replace"))
                return "json"
            except Exception:
                return None
        if data[:8] == StickerManager._STICKER_PNG_MAGIC:
            return "png"
        return None

    def _get_timestamp(self) -> int:
        return timestamp_ms()

    def _generate_id(self) -> int:
        return generate_id()

    def _validate_pack_name(self, name: str) -> str:
        if not name or not name.strip():
            raise InvalidPackNameError("Pack name cannot be empty")
        name = name.strip()
        if len(name) > self._config.get("max_pack_name_length", 50):
            raise InvalidPackNameError(
                f"Pack name cannot exceed {self._config.get('max_pack_name_length', 50)} characters"
            )
        return name

    def _validate_sticker_name(self, name: str) -> str:
        if not name or not name.strip():
            raise InvalidStickerNameError("Sticker name cannot be empty")
        name = name.strip()
        if len(name) > self._config.get("max_sticker_name_length", 30):
            raise InvalidStickerNameError(
                f"Sticker name cannot exceed {self._config.get('max_sticker_name_length', 30)} characters"
            )
        if not re.match(r"^[a-zA-Z0-9_\-]+$", name):
            raise InvalidStickerNameError(
                "Sticker name can only contain letters, numbers, underscores, and hyphens"
            )
        return name

    def create_sticker_from_file(
        self,
        user_id: int,
        pack_id: int,
        name: str,
        image_data: bytes,
        content_type: str,
        tags: Optional[List[str]] = None,
        related_emoji: Optional[str] = None,
    ) -> Sticker:
        """
        Create a sticker from an uploaded file.
        """
        if not self._media:
            raise RuntimeError("Media module not configured")

        # Determine the real format from the file's magic bytes, not the
        # client-declared Content-Type. A declared ``image/png`` whose bytes
        # are actually SVG/HTML must be rejected (stored-XSS vector). The
        # declared Content-Type is then required to be consistent with what the
        # bytes actually are.
        detected = self._detect_sticker_format(image_data)
        if content_type == "application/json":
            if detected != "json":
                raise InvalidStickerFormatError(
                    "Content-Type is JSON but file is not valid JSON",
                    content_type,
                    ["json"],
                )
            sticker_format = StickerFormat.LOTTIE
        elif content_type in ("image/png", "image/apng"):
            if detected != "png":
                raise InvalidStickerFormatError(
                    "Content-Type is PNG/APNG but file is not a PNG image",
                    content_type,
                    ["png", "apng"],
                )
            sticker_format = (
                StickerFormat.APNG
                if content_type == "image/apng"
                else StickerFormat.PNG
            )
        else:
            allowed = self._config.get("allowed_formats", ["png", "apng", "json"])
            raise InvalidStickerFormatError(
                f"Unsupported content type: {content_type}", content_type, allowed
            )

        # Reject oversized uploads before contacting storage.
        max_size = self._config.get("max_sticker_size", 524288)
        if len(image_data) > max_size:
            raise StickerTooLargeError(
                f"Sticker exceeds maximum size of {max_size} bytes",
                max_size,
                len(image_data),
            )

        # Validate the target pack and the caller's permission before uploading
        # so a denied or invalid request cannot leave an orphaned media object.
        pack = self.get_pack(pack_id, user_id)
        if not pack:
            raise PackNotFoundError("Sticker pack not found")
        if (
            pack.pack_type == PackType.SERVER
            and pack.server_id
            and not self._check_server_permission(user_id, pack.server_id)
        ):
            raise PermissionDeniedError(
                "Missing permission to manage server", "server.manage"
            )

        filename = f"sticker_{name}.{content_type.split('/')[-1]}"

        # Upload
        result = self._media.upload_file(user_id, image_data, filename, content_type)
        try:
            return self.add_sticker(
                user_id=user_id,
                pack_id=pack_id,
                name=name,
                format=sticker_format,
                url=result.url,
                size=len(image_data),
                tags=tags,
                related_emoji=related_emoji,
                width=result.metadata.get("width") if result.metadata else None,
                height=result.metadata.get("height") if result.metadata else None,
            )
        except Exception:
            # Roll back the just-uploaded file so storage does not accumulate
            # orphaned media objects when validation (size/format/name/limit)
            # fails after the bytes were already written.
            try:
                if hasattr(self._media, "delete_file_permanently"):
                    self._media.delete_file_permanently(user_id, int(result.file_id))
                elif hasattr(self._media, "delete_file"):
                    self._media.delete_file(user_id, int(result.file_id))
            except Exception as cleanup_err:
                logger.warning(
                    "Failed to clean up orphaned sticker media %s: %s",
                    getattr(result, "file_id", "?"),
                    cleanup_err,
                )
            raise

    def _check_server_permission(self, user_id: int, server_id: int) -> bool:
        if not self._servers:
            return True
        return self._servers.has_permission(user_id, server_id, "server.manage")

    def _user_owns_pack(self, user_id: int, pack: "StickerPack") -> bool:
        """True when ``user_id`` created the pack (personal / purchased packs)."""
        return pack.created_by == user_id

    def _is_server_member(self, user_id: int, server_id: int) -> bool:
        if not self._servers:
            return True
        return self._servers.get_member(server_id, user_id) is not None

    def create_pack(
        self,
        user_id: int,
        name: str,
        description: Optional[str] = None,
        server_id: Optional[int] = None,
        pack_type: PackType = PackType.SERVER,
    ) -> StickerPack:
        name = self._validate_pack_name(name)
        if description and len(description) > self._config.get(
            "max_pack_description_length", 200
        ):
            description = description[
                : self._config.get("max_pack_description_length", 200)
            ]
        if pack_type == PackType.SERVER:
            if not server_id:
                raise ServerNotFoundError("Server ID required for server packs")
            if not self._check_server_permission(user_id, server_id):
                raise PermissionDeniedError(
                    "Missing permission to manage server", "server.manage"
                )
            count_row = self._db.fetch_one(
                "SELECT COUNT(*) as count FROM sticker_packs WHERE server_id = ?",
                (server_id,),
            )
            max_packs = self._config.get("max_packs_per_server", 50)
            if count_row and count_row["count"] >= max_packs:
                raise PackLimitError(
                    f"Server has reached maximum of {max_packs} sticker packs",
                    max_packs,
                    count_row["count"],
                )
        elif pack_type == PackType.PERSONAL:
            # Personal packs were previously created as SERVER packs with a NULL
            # server_id and therefore bypassed the per-server limit entirely,
            # letting a single user create an unbounded number of packs (S2).
            # M6: the quota must also count those legacy rows — a SERVER-typed
            # pack with NULL server_id owned by this user is a personal pack
            # in everything but the enum value, and ignoring it lets legacy
            # users keep minting packs past the cap.
            count_row = self._db.fetch_one(
                "SELECT COUNT(*) as count FROM sticker_packs "
                "WHERE created_by = ? AND (pack_type = ? OR "
                "(pack_type = ? AND server_id IS NULL))",
                (user_id, PackType.PERSONAL.value, PackType.SERVER.value),
            )
            max_packs = self._config.get("max_packs_per_user", 100)
            if count_row and count_row["count"] >= max_packs:
                raise PackLimitError(
                    f"User has reached maximum of {max_packs} personal sticker packs",
                    max_packs,
                    count_row["count"],
                )
        now = self._get_timestamp()
        pack_id = self._generate_id()

        # ``description_encrypted`` is the schema column that holds
        # either the encrypted bytes (production, when encryption is
        # enabled) or the plaintext (tests / non-encrypted
        # deployments).  Previously the non-encrypted branch left
        # the column as ``NULL``, which then tripped
        # ``assert pack.description == "..."`` in the test suite
        # because ``_row_to_pack`` only returns a description when
        # the column is non-empty.
        description_encrypted = None
        if description:
            if self._encrypt_descriptions:
                from src.utils.encryption import encrypt_data

                description_encrypted = encrypt_data(description)
            else:
                # Plaintext round-trip for environments without
                # description encryption.
                description_encrypted = description

        self._db.execute(
            "INSERT INTO sticker_packs (id, name, description_encrypted, pack_type, server_id, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                pack_id,
                name,
                description_encrypted,
                pack_type.value,
                server_id,
                user_id,
                now,
                now,
            ),
        )
        result = self.get_pack(pack_id, user_id)
        assert result is not None  # Should exist since we just created it
        return result

    def get_pack(
        self, pack_id: int, user_id: Optional[int] = None
    ) -> Optional[StickerPack]:
        """Look up a sticker pack by ID.

        `user_id` is optional: when provided we enforce server-membership
        visibility for non-public packs, when omitted (``None``) we simply
        return the row so admin / cron / API list-style callers can fetch
        without impersonating anyone.
        """
        row = self._db.fetch_one(
            "SELECT p.*, COUNT(s.id) as sticker_count FROM sticker_packs p LEFT JOIN sticker_stickers s ON p.id = s.pack_id WHERE p.id = ? GROUP BY p.id",
            (pack_id,),
        )
        if not row:
            return None
        if (
            row["pack_type"] == PackType.SERVER.value
            and row["server_id"]
            and user_id is not None
            and not self._is_server_member(user_id, row["server_id"])
        ):
            return None
        return self._row_to_pack(row)

    def get_server_packs(self, user_id: int, server_id: int) -> List[StickerPack]:
        if not self._is_server_member(user_id, server_id):
            raise PermissionDeniedError("Not a member of this server")
        rows = self._db.fetch_all(
            "SELECT p.*, COUNT(s.id) as sticker_count FROM sticker_packs p LEFT JOIN sticker_stickers s ON p.id = s.pack_id WHERE p.server_id = ? AND p.pack_type = ? GROUP BY p.id ORDER BY p.created_at DESC",
            (server_id, PackType.SERVER.value),
        )
        return [self._row_to_pack(row) for row in rows]

    def _purge_sticker_media(self, user_id: int, url: Optional[str]) -> None:
        """Best-effort deletion of the media file backing a sticker's URL.

        Stickers persist only the public ``url`` (not the media file id), so the
        underlying file is resolved via the stored filename and removed. This
        prevents orphaned media from accumulating after a sticker/pack is
        deleted. (S1 fix)
        """
        if not url or not self._media:
            return
        filename = url.rsplit("/", 1)[-1] if "/" in url else url
        if not filename:
            return
        try:
            media_file = self._media.get_file_by_filename(filename)
            if media_file is None:
                return
            file_id = int(media_file.id)
            try:
                if hasattr(self._media, "delete_file_permanently"):
                    self._media.delete_file_permanently(user_id, file_id)
                else:
                    self._media.delete_file(user_id, file_id)
            except Exception as del_err:
                logger.warning("Failed to delete sticker media %s: %s", url, del_err)
        except Exception as e:
            logger.warning(f"Failed to resolve sticker media for {url}: {e}")

    def delete_pack(self, user_id: int, pack_id: int) -> bool:
        pack = self.get_pack(pack_id, user_id)
        if not pack:
            raise PackNotFoundError("Sticker pack not found")
        if pack.pack_type == PackType.DEFAULT:
            raise PermissionDeniedError("Cannot delete default packs")
        if pack.pack_type == PackType.SERVER and pack.server_id:
            if not self._check_server_permission(user_id, pack.server_id):
                raise PermissionDeniedError(
                    "Missing permission to manage server", "server.manage"
                )
        elif not self._user_owns_pack(user_id, pack):
            raise PermissionDeniedError("Can only delete own packs")
        sticker_rows = self._db.fetch_all(
            "SELECT url FROM sticker_stickers WHERE pack_id = ?", (pack_id,)
        )
        self._db.execute("DELETE FROM sticker_stickers WHERE pack_id = ?", (pack_id,))
        self._db.execute("DELETE FROM sticker_packs WHERE id = ?", (pack_id,))
        for row in sticker_rows:
            self._purge_sticker_media(user_id, row["url"])
        return True

    def add_sticker(
        self,
        user_id: int,
        pack_id: int,
        name: str,
        format: StickerFormat,
        url: str,
        size: int,
        tags: Optional[List[str]] = None,
        related_emoji: Optional[str] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ) -> Sticker:
        pack = self.get_pack(pack_id, user_id)
        if not pack:
            raise PackNotFoundError("Sticker pack not found")
        if pack.pack_type == PackType.SERVER and pack.server_id:
            if not self._check_server_permission(user_id, pack.server_id):
                raise PermissionDeniedError(
                    "Missing permission to manage server", "server.manage"
                )
        elif not self._user_owns_pack(user_id, pack):
            raise PermissionDeniedError("Can only add stickers to own packs")
        name = self._validate_sticker_name(name)
        # M4: sticker URLs render into <img src> contexts — reject active
        # schemes (javascript:, data:, vbscript:, ...) and only allow
        # http(s) or schemeless (relative) URLs.
        if isinstance(url, str):
            _scheme = url.strip().split(":", 1)[0].lower() if ":" in url else ""
            if _scheme in ("javascript", "data", "vbscript", "file", "ftp") or (
                ":" in url and _scheme not in ("http", "https", "")
            ):
                # A bare ':' with an unknown scheme (or no '://') is not an
                # http(s) URL — reject. Schemeless relative values pass.
                if _scheme not in ("http", "https"):
                    raise InvalidStickerNameError(
                        "Sticker URL must be an http(s) URL or site-relative path"
                    )
        allowed = self._config.get("allowed_formats", ["png", "apng", "json"])
        if format.value not in allowed:
            raise InvalidStickerFormatError(
                f"Format {format.value} not allowed", format.value, allowed
            )
        max_size = self._config.get("max_sticker_size", 524288)
        if size > max_size:
            raise StickerTooLargeError(
                f"Sticker exceeds maximum size of {max_size} bytes", max_size, size
            )
        count_row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM sticker_stickers WHERE pack_id = ?",
            (pack_id,),
        )
        max_stickers = self._config.get("max_stickers_per_pack", 50)
        if count_row and count_row["count"] >= max_stickers:
            raise StickerLimitError(
                f"Pack has reached maximum of {max_stickers} stickers",
                max_stickers,
                count_row["count"],
            )
        now = self._get_timestamp()
        sticker_id = self._generate_id()
        self._db.execute(
            "INSERT INTO sticker_stickers (id, pack_id, name, format, tags, related_emoji, url, size, width, height, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sticker_id,
                pack_id,
                name,
                format.value,
                json.dumps(tags) if tags else None,
                related_emoji,
                url,
                size,
                width,
                height,
                now,
            ),
        )
        self._db.execute(
            "UPDATE sticker_packs SET updated_at = ? WHERE id = ?", (now, pack_id)
        )
        result = self.get_sticker(sticker_id)
        assert result is not None  # Should exist since we just created it
        return result

    def get_sticker(self, sticker_id: int) -> Optional[Sticker]:
        row = self._db.fetch_one(
            "SELECT s.*, COUNT(u.id) as usage_count FROM sticker_stickers s LEFT JOIN sticker_usage u ON s.id = u.sticker_id WHERE s.id = ? GROUP BY s.id",
            (sticker_id,),
        )
        return self._row_to_sticker(row) if row else None

    def get_pack_stickers(
        self,
        *args: Any,
        pack_id: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> List[Sticker]:
        """Return every sticker in a pack.

        Accepts every call shape the codebase has shipped with so a
        caller that quietly sticks to the older positional order
        does not silently trigger a 500 via an impossible
        ``pack_id`` lookup.  Supported forms:

        * ``get_pack_stickers(user.id, pack.id)`` — canonical
          ``(user_id, pack_id)`` ordering used by the route handler
          in ``src/api/routes/stickers.py`` and by the module-level
          wrapper in ``src/core/stickers/__init__.py``.
        * ``get_pack_stickers(pack.id)`` — legacy single-arg form
          used by ``tests/stickers/test_operations.py``.
        * ``get_pack_stickers(pack_id=..., user_id=...)`` — explicit
          keyword form.

        Single SQL plan, single error path; only the dispatch line
        differs from the canonical body.
        """
        # Dispatch positional args into ``pack_id``/``user_id``.
        if args:
            if len(args) == 1 and pack_id is None:
                pack_id = args[0]
            elif len(args) == 2 and user_id is None and pack_id is None:
                user_id, pack_id = args[0], args[1]
            else:
                raise TypeError(
                    "get_pack_stickers() takes 1, 2, or keyword args; "
                    f"got {len(args)} positional args (pack_id={pack_id!r})"
                )
        if pack_id is None:
            raise TypeError("get_pack_stickers() requires a pack_id")
        pack = self.get_pack(pack_id, user_id)
        if not pack:
            raise PackNotFoundError("Sticker pack not found")
        rows = self._db.fetch_all(
            "SELECT s.*, COUNT(u.id) as usage_count FROM sticker_stickers s LEFT JOIN sticker_usage u ON s.id = u.sticker_id WHERE s.pack_id = ? GROUP BY s.id ORDER BY s.name",
            (pack_id,),
        )
        return [self._row_to_sticker(row) for row in rows]

    # ----------------------------------------------------------------- #
    # Canonical sticker removal entry point                            #
    # ----------------------------------------------------------------- #
    def delete_sticker(self, user_id: int, sticker_id: int) -> bool:
        """Remove a sticker from its pack (canonical name).

        Permission semantics match :meth:`add_sticker`: server packs
        require ``server.manage`` on the owning server, otherwise the
        caller must be the pack's creator.
        """
        sticker = self.get_sticker(sticker_id)
        if not sticker:
            raise StickerNotFoundError("Sticker not found")
        pack = self.get_pack(sticker.pack_id, user_id)
        if not pack:
            raise PackNotFoundError("Sticker pack not found")
        if pack.pack_type == PackType.SERVER and pack.server_id:
            if not self._check_server_permission(user_id, pack.server_id):
                raise PermissionDeniedError(
                    "Missing permission to manage server", "server.manage"
                )
        elif not self._user_owns_pack(user_id, pack):
            raise PermissionDeniedError("Can only remove stickers from own packs")
        self._db.execute("DELETE FROM sticker_stickers WHERE id = ?", (sticker_id,))
        self._db.execute(
            "UPDATE sticker_packs SET updated_at = ? WHERE id = ?",
            (self._get_timestamp(), pack.id),
        )
        self._purge_sticker_media(user_id, sticker.url)
        logger.debug("Sticker %s deleted by user %s", sticker_id, user_id)
        return True

    def update_sticker(
        self,
        user_id: int,
        sticker_id: int,
        *,
        name: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> Sticker:
        """Update editable fields on a sticker owned by `user_id`.

        Only ``name`` and ``tags`` are mutable from this endpoint —
        immutable fields like ``url``/``size``/``format`` are fixed at
        creation.  Raises :class:`PermissionDeniedError` if the caller
        does not own the parent pack (or has ``server.manage`` on the
        owning server for server packs).
        """
        sticker = self.get_sticker(sticker_id)
        if not sticker:
            raise StickerNotFoundError("Sticker not found")
        pack = self.get_pack(sticker.pack_id, user_id)
        if not pack:
            raise PackNotFoundError("Sticker pack not found")
        if pack.pack_type == PackType.SERVER and pack.server_id:
            if not self._check_server_permission(user_id, pack.server_id):
                raise PermissionDeniedError(
                    "Missing permission to manage server", "server.manage"
                )
        elif not self._user_owns_pack(user_id, pack):
            raise PermissionDeniedError("Can only update stickers in own packs")

        updates: Dict[str, Any] = {}
        params: List[Any] = []
        if name is not None:
            updates["name"] = self._validate_sticker_name(name)
            params.append(updates["name"])
        if tags is not None:
            # Match :meth:`add_sticker`'s NULL convention: empty lists
            # are stored as NULL rather than ``[]`` so a downstream
            # ``json.loads(t) or []`` consumer gets the same result
            # regardless of which writer produced the row.
            updates["tags"] = json.dumps(list(tags)) if list(tags) else None
            params.append(updates["tags"])
        if not updates:
            return sticker

        set_clause = ", ".join(f"{col} = ?" for col in updates.keys())
        params.append(sticker_id)
        self._db.execute(
            f"UPDATE sticker_stickers SET {set_clause} WHERE id = ?",
            tuple(params),
        )
        self._db.execute(
            "UPDATE sticker_packs SET updated_at = ? WHERE id = ?",
            (self._get_timestamp(), pack.id),
        )
        refreshed = self.get_sticker(sticker_id)
        assert refreshed is not None  # only None if the row was deleted
        return refreshed

    def update_pack(
        self,
        user_id: int,
        pack_id: int,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> StickerPack:
        """Update editable fields on a sticker pack owned by `user_id`."""
        pack = self.get_pack(pack_id, user_id)
        if not pack:
            raise PackNotFoundError("Sticker pack not found")
        if pack.pack_type == PackType.DEFAULT:
            raise PermissionDeniedError("Cannot modify default packs")
        if pack.pack_type == PackType.SERVER and pack.server_id:
            if not self._check_server_permission(user_id, pack.server_id):
                raise PermissionDeniedError(
                    "Missing permission to manage server", "server.manage"
                )
        elif not self._user_owns_pack(user_id, pack):
            raise PermissionDeniedError("Can only update own packs")

        updates: Dict[str, Any] = {}
        params: List[Any] = []
        if name is not None:
            updates["name"] = self._validate_pack_name(name)
            params.append(updates["name"])
        if description is not None:
            max_len = self._config.get("max_pack_description_length", 200)
            desc = (description or "")[:max_len]
            description_encrypted = None
            if desc and self._encrypt_descriptions:
                from src.utils.encryption import encrypt_data

                description_encrypted = encrypt_data(desc)
                updates["description_encrypted"] = description_encrypted
                params.append(description_encrypted)
            else:
                updates["description_encrypted"] = desc
                params.append(desc)
        if not updates:
            return pack

        set_clause = ", ".join(f"{col} = ?" for col in updates.keys())
        # ``pack_id`` is appended below alongside ``updated_at``;
        # do NOT also push it onto ``params`` here — that produces
        # one extra bound arg and triggers
        # ``sqlite3.ProgrammingError: Incorrect number of bindings
        # supplied. The current statement uses 3, and there are 4
        # supplied.`` in ``test_update_pack_name`` and friends.
        self._db.execute(
            f"UPDATE sticker_packs SET {set_clause}, updated_at = ? WHERE id = ?",
            tuple(params) + (self._get_timestamp(), pack_id),
        )
        refreshed = self.get_pack(pack_id, user_id)
        assert refreshed is not None
        return refreshed

    def get_user_packs(self, user_id: int) -> List[StickerPack]:
        """Return every personal / user-owned pack for `user_id`.

        Filters by ``server_id IS NULL AND created_by = user_id`` so it
        captures every pack a user owns irrespective of ``pack_type``
        (SERVER-personal, PURCHASED-personal, etc.).  System-owned
        DEFAULT packs (``created_by=0``) are excluded by the
        ``created_by=?`` filter.
        """
        rows = self._db.fetch_all(
            """SELECT p.*, COUNT(s.id) as sticker_count
               FROM sticker_packs p
               LEFT JOIN sticker_stickers s ON p.id = s.pack_id
               WHERE p.created_by = ?
                 AND p.server_id IS NULL
               GROUP BY p.id
               ORDER BY p.created_at DESC""",
            (user_id,),
        )
        return [self._row_to_pack(row) for row in rows]
