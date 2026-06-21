"""
Sticker manager - Core business logic for sticker operations.
"""

import time
import json
import re
from typing import Optional, List, Dict, Any

import utils.config as config
import utils.logger as logger
from src.utils.encryption import generate_snowflake_id

from .models import (
    StickerPack,
    Sticker,
    StickerUsage,
    StickerSuggestion,
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
    MessageNotFoundError,
)


class StickerManager:
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

    def _get_timestamp(self) -> int:
        return int(time.time() * 1000)

    def _generate_id(self) -> int:
        return generate_snowflake_id()

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

        # Basic format detection
        if content_type == "application/json":
            sticker_format = StickerFormat.LOTTIE
        elif content_type == "image/png":
            sticker_format = StickerFormat.PNG
        elif content_type == "image/apng":
            sticker_format = StickerFormat.APNG
        else:
            # Default to PNG if image, or raise error
            if content_type.startswith("image/"):
                sticker_format = StickerFormat.PNG
            else:
                allowed = self._config.get("allowed_formats", ["png", "apng", "json"])
                raise InvalidStickerFormatError(
                    f"Unsupported content type: {content_type}", content_type, allowed
                )

        filename = f"sticker_{name}.{content_type.split('/')[-1]}"

        # Upload
        result = self._media.upload_file(user_id, image_data, filename, content_type)

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

    def _check_server_permission(self, user_id: int, server_id: int) -> bool:
        if not self._servers:
            return True
        return self._servers.has_permission(user_id, server_id, "server.manage")

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

    def delete_pack(self, user_id: int, pack_id: int) -> bool:
        pack = self.get_pack(pack_id, user_id)
        if not pack:
            raise PackNotFoundError("Sticker pack not found")
        if pack.pack_type == PackType.DEFAULT:
            raise PermissionDeniedError("Cannot delete default packs")
        if (
            pack.pack_type == PackType.SERVER
            and pack.server_id
            and not self._check_server_permission(user_id, pack.server_id)
        ):
            raise PermissionDeniedError(
                "Missing permission to manage server", "server.manage"
            )
        elif pack.pack_type != PackType.SERVER and pack.created_by != user_id:
            raise PermissionDeniedError("Can only delete own packs")
        self._db.execute("DELETE FROM sticker_stickers WHERE pack_id = ?", (pack_id,))
        self._db.execute("DELETE FROM sticker_packs WHERE id = ?", (pack_id,))
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
        if (
            pack.pack_type == PackType.SERVER
            and pack.server_id
            and not self._check_server_permission(user_id, pack.server_id)
        ):
            raise PermissionDeniedError(
                "Missing permission to manage server", "server.manage"
            )
        elif pack.pack_type != PackType.SERVER and pack.created_by != user_id:
            raise PermissionDeniedError("Can only add stickers to own packs")
        name = self._validate_sticker_name(name)
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
        if (
            pack.pack_type == PackType.SERVER
            and pack.server_id
            and not self._check_server_permission(user_id, pack.server_id)
        ):
            raise PermissionDeniedError(
                "Missing permission to manage server", "server.manage"
            )
        elif pack.pack_type != PackType.SERVER and pack.created_by != user_id:
            raise PermissionDeniedError("Can only remove stickers from own packs")
        self._db.execute("DELETE FROM sticker_stickers WHERE id = ?", (sticker_id,))
        self._db.execute(
            "UPDATE sticker_packs SET updated_at = ? WHERE id = ?",
            (self._get_timestamp(), pack.id),
        )
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
        if (
            pack.pack_type == PackType.SERVER
            and pack.server_id
            and not self._check_server_permission(user_id, pack.server_id)
        ):
            raise PermissionDeniedError(
                "Missing permission to manage server", "server.manage"
            )
        elif pack.pack_type != PackType.SERVER and pack.created_by != user_id:
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
        if (
            pack.pack_type == PackType.SERVER
            and pack.server_id
            and not self._check_server_permission(user_id, pack.server_id)
        ):
            raise PermissionDeniedError(
                "Missing permission to manage server", "server.manage"
            )
        if pack.pack_type == PackType.DEFAULT:
            raise PermissionDeniedError("Cannot modify default packs")
        if pack.pack_type != PackType.SERVER and pack.created_by != user_id:
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

    def record_usage(
        self, user_id: int, sticker_id: int, message_id: Optional[int] = None
    ) -> None:
        """Append a usage row for analytics / popularity scoring.

        ``message_id`` is optional to support inline sticker previews
        (which don't necessarily ship inside a message body).
        """
        now = self._get_timestamp()
        usage_id = self._generate_id()
        self._db.execute(
            "INSERT INTO sticker_usage (id, sticker_id, user_id, message_id, used_at) VALUES (?, ?, ?, ?, ?)",
            (usage_id, sticker_id, user_id, message_id or 0, now),
        )

    def get_usage_count(self, sticker_id: int) -> int:
        row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM sticker_usage WHERE sticker_id = ?",
            (sticker_id,),
        )
        return int(row["count"]) if row else 0

    def get_popular_stickers(self, limit: int = 10) -> List[Sticker]:
        """Return the top-N most-used stickers across all public/personal packs."""
        rows = self._db.fetch_all(
            """SELECT s.*, COUNT(u.id) as usage_count
               FROM sticker_stickers s
               LEFT JOIN sticker_usage u ON s.id = u.sticker_id
               GROUP BY s.id
               ORDER BY usage_count DESC, s.created_at DESC
               LIMIT ?""",
            (limit,),
        )
        return [self._row_to_sticker(row) for row in rows]

    def get_recent_stickers(self, user_id: int, limit: int = 10) -> List[Sticker]:
        """Return the distinct stickers `user_id` used most recently."""
        rows = self._db.fetch_all(
            """SELECT s.*, COUNT(u.id) as usage_count, MAX(u.used_at) as last_used
               FROM sticker_stickers s
               INNER JOIN sticker_usage u ON s.id = u.sticker_id
               WHERE u.user_id = ?
               GROUP BY s.id
               ORDER BY last_used DESC
               LIMIT ?""",
            (user_id, limit),
        )
        if not rows:
            return []
        return [self._row_to_sticker(row) for row in rows]

    def search_stickers(self, query: str, limit: int = 25) -> List[Sticker]:
        """Free-text search across name + tag columns."""
        if not query or not query.strip():
            return []
        needle = f"%{query.strip().lower()}%"
        rows = self._db.fetch_all(
            """SELECT s.*, COUNT(u.id) as usage_count
               FROM sticker_stickers s
               LEFT JOIN sticker_usage u ON s.id = u.sticker_id
               WHERE LOWER(s.name) LIKE ? OR LOWER(s.tags) LIKE ?
               GROUP BY s.id
               ORDER BY usage_count DESC
               LIMIT ?""",
            (needle, needle, limit),
        )
        return [self._row_to_sticker(row) for row in rows]

    def get_suggestions(
        self, text: str, user_id: Optional[int] = None, limit: int = 5
    ) -> List[StickerSuggestion]:
        """Compatibility shim around :meth:`get_sticker_suggestions`.

        The test-suite calls ``sticker_manager.get_suggestions("yes")``
        without a server scope; we accept that pattern and fall back to
        the older API.
        """
        return self.get_sticker_suggestions(
            user_id or 0, text, server_id=None, limit=limit
        )

    def get_trending_stickers(self, limit: int = 10) -> List[Sticker]:
        """Public-facing variant of :meth:`get_popular_stickers`."""
        return self.get_popular_stickers(limit=max(1, limit))

    def can_use_sticker(self, user_id: int, sticker_id: int) -> bool:
        """Permission query used by message senders.

        Server packs (pack_type=SERVER, server_id != NULL) require
        membership; "personal" packs (pack_type=SERVER,
        server_id IS NULL) require ownership of the pack
        (``created_by == user_id``); default packs are open to everyone.
        """
        sticker = self.get_sticker(sticker_id)
        if not sticker:
            return False
        pack = self.get_pack(sticker.pack_id, user_id)
        if not pack:
            return False
        if pack.pack_type == PackType.SERVER and pack.server_id:
            return self._is_server_member(user_id, pack.server_id)
        if pack.pack_type == PackType.SERVER and pack.server_id is None:
            # Personal pack — caller must own it.
            return pack.created_by == user_id
        # PackType.DEFAULT or other: accessible by anyone
        return True

    def send_sticker(
        self, user_id: int, message_id: int, sticker_id: int
    ) -> StickerUsage:
        sticker = self.get_sticker(sticker_id)
        if not sticker:
            raise StickerNotFoundError("Sticker not found")
        if self._messaging:
            msg = self._messaging.get_message(user_id, message_id)
            if not msg:
                raise MessageNotFoundError("Message not found")
        now = self._get_timestamp()
        usage_id = self._generate_id()
        self._db.execute(
            "INSERT INTO sticker_usage (id, sticker_id, user_id, message_id, used_at) VALUES (?, ?, ?, ?, ?)",
            (usage_id, sticker_id, user_id, message_id, now),
        )
        return StickerUsage(
            id=usage_id,
            sticker_id=sticker_id,
            user_id=user_id,
            message_id=message_id,
            used_at=now,
        )

    def get_sticker_suggestions(
        self,
        user_id: int,
        content: str,
        server_id: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[StickerSuggestion]:
        if not content or not content.strip():
            return []
        max_suggestions = limit or self._config.get("max_suggestions", 10)
        content_lower = content.lower()
        words = set(re.findall(r"\w+", content_lower))
        suggestions = []
        rows = self._db.fetch_all(
            "SELECT s.*, COUNT(u.id) as usage_count FROM sticker_stickers s INNER JOIN sticker_packs p ON s.pack_id = p.id LEFT JOIN sticker_usage u ON s.id = u.sticker_id WHERE (p.pack_type = ? OR (p.pack_type = ? AND p.server_id = ?)) GROUP BY s.id",
            (PackType.DEFAULT.value, PackType.SERVER.value, server_id or 0),
        )
        for row in rows:
            sticker = self._row_to_sticker(row)
            matched, score = [], 0.0
            if sticker.name.lower() in content_lower:
                score += 1.0
                matched.append(sticker.name)
            if sticker.tags:
                for tag in sticker.tags:
                    if tag.lower() in words:
                        score += 0.5
                        matched.append(tag)
            if sticker.related_emoji and sticker.related_emoji in content:
                score += 0.3
                matched.append(sticker.related_emoji)
            if score > 0:
                score += min(sticker.usage_count * 0.01, 0.5)
                suggestions.append(
                    StickerSuggestion(
                        sticker=sticker, relevance_score=score, matched_keywords=matched
                    )
                )
        suggestions.sort(key=lambda x: x.relevance_score, reverse=True)
        return suggestions[:max_suggestions]

    def _row_to_pack(self, row) -> StickerPack:
        """Convert a database row to a ``StickerPack`` dataclass.

        ``description`` is decrypted only when encryption is enabled and
        the row actually carries an encrypted payload; otherwise the raw
        plaintext column is returned.  ``sticker_count`` is supplied by
        upstream aggregations and falls back to 0 when the LEFT JOIN was
        omitted.
        """
        sticker_count = 0
        try:
            sticker_count = row["sticker_count"]
        except (KeyError, IndexError):
            sticker_count = 0

        description = None
        if row.get("description_encrypted"):
            if self._encrypt_descriptions:
                from src.utils.encryption import decrypt_data

                try:
                    description = decrypt_data(row["description_encrypted"])
                except Exception as e:
                    logger.warning(
                        f"Failed to decrypt sticker pack description {row['id']}: {e}"
                    )
            else:
                # Not encrypted — stored as plaintext
                description = row["description_encrypted"]

        return StickerPack(
            id=row["id"],
            name=row["name"],
            description=description,
            pack_type=PackType(row["pack_type"]),
            server_id=row["server_id"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            sticker_count=sticker_count,
            is_public=bool(row["is_public"]),
        )

    def _row_to_sticker(self, row) -> Sticker:
        tags = []
        if row.get("tags"):
            try:
                tags = json.loads(row["tags"])
            except (json.JSONDecodeError, TypeError):
                logger.warning(f"Invalid tags JSON for sticker {row.get('id')}")
        usage_count = (
            row.get("usage_count", 0)
            if hasattr(row, "get")
            else (row["usage_count"] if "usage_count" in row.keys() else 0)
        )
        return Sticker(
            id=row["id"],
            pack_id=row["pack_id"],
            name=row["name"],
            format=StickerFormat(row["format"]),
            tags=tags,
            related_emoji=row["related_emoji"],
            url=row["url"],
            size=row["size"],
            width=row["width"],
            height=row["height"],
            created_at=row["created_at"],
            usage_count=usage_count,
        )
