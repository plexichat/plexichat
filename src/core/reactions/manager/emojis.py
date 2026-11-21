from typing import Optional, List, Dict
from dataclasses import replace
from io import BytesIO
from PIL import Image
import utils.logger as logger
from src.core.base import SnowflakeID
from .emoji_lookup_cache import (
    EmojiCatalogCache,
    DEFAULT_EMOJI_TTL_SECONDS,
    DEFAULT_EMOJI_MAX_ENTRIES,
    DEFAULT_EMOJI_MAX_BYTES,
)
from ..models import CustomEmoji
from ..exceptions import (
    CustomEmojiNotFoundError,
    PermissionDeniedError,
    EmojiLimitError,
    EmojiNameExistsError,
    EmojiFileSizeError,
    InvalidEmojiFileError,
)


from .protocol import ReactionProtocol


# One statement re-checks the per-server emoji cap at the instant of the
# insert. The old flow called ``_check_emoji_limits`` (a SELECT COUNT) and only
# then began a transaction and INSERTed; two concurrent creators could both
# read a count below the cap and both insert, pushing the server over the
# configured limit (a TOCTOU quota bypass). The guard subquery sees the table
# state *before* the new row exists, so exactly the callers that still fit get
# a row and ``rowcount == 0`` is an authoritative "limit reached" signal.
# SQLite serialises this single statement with its write lock; PostgreSQL's
# READ COMMITTED snapshot would let two statements both pass, so
# create_custom_emoji() additionally takes an advisory xact lock (see there).
_ATOMIC_EMOJI_INSERT_SQL = """
INSERT INTO react_custom_emoji
    (id, server_id, name, animated, url, size, width, height,
     created_by, available, created_at)
SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?
WHERE (SELECT COUNT(*) FROM react_custom_emoji
       WHERE server_id = ? AND animated = ?) < ?
"""


class EmojiOpsMixin(ReactionProtocol):
    """Custom-emoji CRUD plus a small bounded cache over its read queries.

    ``get_server_custom_emojis`` (a JOIN with ``auth_users``), ``get_custom_emoji``
    and ``get_emoji_counts`` (a single grouped COUNT) are resolved repeatedly by
    dispatch helpers, enrichment code and direct core callers.  The cache is created
    lazily on the manager instance so no change to ``ReactionBase`` is needed;
    every successful mutation invalidates the entries it can affect.

    The cached payloads are server data, not per-user data, so the keys carry no
    credential segment: authorization is enforced by the callers/HTTP layer
    before these helpers are reached, and its response cache keeps the
    credential-partitioned keys.  Values are copied on the way in/out so a
    caller mutating a returned list/dict/dataclass cannot poison the cache.
    """

    _EMOJI_CATALOG_CACHE_ATTR = "_emoji_catalog_cache_instance"

    def _emoji_catalog_cache(self) -> EmojiCatalogCache:
        cache = getattr(self, self._EMOJI_CATALOG_CACHE_ATTR, None)
        if cache is None:
            cache = EmojiCatalogCache(
                ttl_seconds=float(
                    self._config.get(
                        "emoji_catalog_cache_ttl", DEFAULT_EMOJI_TTL_SECONDS
                    )
                ),
                max_entries=int(
                    self._config.get(
                        "emoji_catalog_cache_max_entries", DEFAULT_EMOJI_MAX_ENTRIES
                    )
                ),
                max_bytes=int(
                    self._config.get(
                        "emoji_catalog_cache_max_bytes", DEFAULT_EMOJI_MAX_BYTES
                    )
                ),
            )
            setattr(self, self._EMOJI_CATALOG_CACHE_ATTR, cache)
        return cache

    def _invalidate_emoji_catalog(
        self,
        server_id: Optional[SnowflakeID] = None,
        emoji_id: Optional[SnowflakeID] = None,
    ) -> None:
        """Drop the list/count entries for a server and/or a detail entry."""
        cache = self._emoji_catalog_cache()
        if server_id is not None:
            cache.invalidate_server(server_id)
        if emoji_id is not None:
            cache.invalidate_emoji(emoji_id)

    def _migrate_emoji_table(self):
        # Only migrate an EXISTING table that predates the new columns. A
        # missing table means a fresh install whose schema is created later -
        # nothing to migrate and no misleading log. Non-sqlite dialects fall
        # back to the column check directly.
        # ``sqlite_master`` does not exist in Postgres; querying it there
        # raised on every startup (logged as a query failure). Use the
        # database's portable existence check instead.
        table_exists = getattr(self._db, "table_exists", None)
        try:
            if callable(table_exists):
                if not table_exists("react_custom_emoji"):
                    return
            elif getattr(self._db, "type", None) != "postgres":
                row = self._db.fetch_one(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'react_custom_emoji'"
                )
                if not row:
                    return
        except Exception as column_error:
            logger.debug(f"emoji migration column probe failed: {column_error}")
        try:
            self._db.execute("SELECT url FROM react_custom_emoji LIMIT 1")
            return  # columns already present
        except Exception as probe_error:
            logger.debug(f"emoji table needs migration: {probe_error}")
        logger.info("Migrating react_custom_emoji table with new columns")
        migrations = [
            "ALTER TABLE react_custom_emoji ADD COLUMN url TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE react_custom_emoji ADD COLUMN size INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE react_custom_emoji ADD COLUMN width INTEGER",
            "ALTER TABLE react_custom_emoji ADD COLUMN height INTEGER",
            "ALTER TABLE react_custom_emoji ADD COLUMN created_by INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE react_custom_emoji ADD COLUMN available INTEGER NOT NULL DEFAULT 1",
        ]
        for sql in migrations:
            try:
                self._db.execute(sql)
            except Exception as e:
                logger.debug(f"Migration step failed (possibly column exists): {e}")

    def _require_manage_server(
        self, user_id: SnowflakeID, server_id: SnowflakeID
    ) -> None:
        """Enforce ``server.manage`` for server-scoped custom-emoji writes.

        SECURITY: fail CLOSED when the servers module is not wired. The
        previous ``if self._servers:`` guard skipped the check entirely in
        that state, so any authenticated caller could create/update/delete a
        server's custom emojis without authorization. This mirrors
        ``_check_server_permission``, which also denies what it cannot
        verify.
        """
        if not self._servers:
            logger.error(
                "Servers module unavailable; denying custom-emoji management "
                f"for user {user_id} in server {server_id}"
            )
            raise PermissionDeniedError(
                "Missing permission to manage server", "server.manage"
            )
        if not self._servers.has_permission(user_id, server_id, "server.manage"):
            raise PermissionDeniedError(
                "Missing permission to manage server", "server.manage"
            )

    def _emoji_limit_for(self, animated: bool) -> int:
        """The configured per-server cap for one emoji kind (single source of truth)."""
        key = "max_animated_emojis_per_server" if animated else "max_emojis_per_server"
        return self._config.get(key, 50)

    def _check_emoji_limits(self, server_id: SnowflakeID, animated: bool) -> None:
        # Best-effort early feedback only. The authoritative check is the
        # atomic INSERT in create_custom_emoji(); this pre-flight just avoids an
        # unnecessary media upload for an obviously-full server.
        max_count = self._emoji_limit_for(animated)
        animated_int = 1 if animated else 0
        row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM react_custom_emoji WHERE server_id = ? AND animated = ?",
            (server_id, animated_int),
        )

        current = row["count"] if row else 0
        if current >= max_count:
            emoji_type = "animated emojis" if animated else "static emojis"
            raise EmojiLimitError(
                f"Server has reached maximum of {max_count} {emoji_type}",
                max_count,
                current,
            )

    def create_custom_emoji(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        name: str,
        image_data: bytes,
        content_type: str,
    ) -> CustomEmoji:
        self._require_manage_server(user_id, server_id)

        name = self._validate_emoji_name(name)

        max_size = self._config.get("max_emoji_size", 262144)
        if len(image_data) > max_size:
            raise EmojiFileSizeError(
                f"Emoji file size exceeds {max_size // 1024}KB limit",
                max_size,
                len(image_data),
            )

        allowed_formats = self._config.get(
            "emoji_allowed_formats", ["image/png", "image/gif", "image/webp"]
        )
        if content_type.lower() not in allowed_formats:
            raise InvalidEmojiFileError(
                f"Invalid format. Allowed: {', '.join(allowed_formats)}"
            )

        try:
            img = Image.open(BytesIO(image_data))
            img.verify()
            width_hint = img.width
            height_hint = img.height

            if width_hint > 1024 or height_hint > 1024:
                raise InvalidEmojiFileError(
                    "Emoji image dimensions too large (max 1024x1024)"
                )

        except Exception as e:
            raise InvalidEmojiFileError(f"Invalid or corrupted image file: {str(e)}")

        animated = content_type.lower() == "image/gif"
        if content_type.lower() == "image/webp":
            animated = b"ANIM" in image_data[:100]
        animated_int = 1 if animated else 0
        max_count = self._emoji_limit_for(animated)

        # Early, advisory quota feedback (avoids a wasted upload). It is NOT the
        # authoritative check: two concurrent creators can both pass it, so the
        # atomic INSERT below is what actually enforces the cap.
        self._check_emoji_limits(server_id, animated)

        # Fast-fail a duplicate name before the (expensive) media upload so an
        # upload is never wasted on a name we already have. UNIQUE(server_id,
        # name) remains authoritative if a concurrent creator wins the same
        # name after this read (see the UNIQUE handler in the except chain).
        existing = self._db.fetch_one(
            "SELECT 1 FROM react_custom_emoji WHERE server_id = ? AND name = ?",
            (server_id, name),
        )
        if existing:
            raise EmojiNameExistsError(
                f"Emoji with name '{name}' already exists in this server"
            )

        url = ""
        width = None
        height = None

        if self._media:
            ext = "gif" if animated else content_type.split("/")[-1]
            filename = f"emoji_{name}.{ext}"
            try:
                result = self._media.upload_file(
                    user_id, image_data, filename, content_type
                )
                url = result.url
                if result.metadata:
                    width = result.metadata.get("width")
                    height = result.metadata.get("height")

                if url:
                    if url.startswith("/"):
                        pass
                    elif url.startswith("http://") or url.startswith("https://"):
                        pass
                    else:
                        logger.error(f"Media module returned unsafe URL: {url}")
                        raise InvalidEmojiFileError(
                            "Media module returned an unsafe URL"
                        )
            except InvalidEmojiFileError:
                raise
            except Exception as e:
                logger.error(f"Failed to upload emoji image: {e}")
                raise InvalidEmojiFileError(f"Failed to upload emoji: {str(e)}")

        # The media upload above is deliberately OUTSIDE the advisory lock and
        # open transaction: it is storage I/O unrelated to the quota guard.
        # Holding the per-(server, kind) lock across it would serialise every
        # emoji creation for the server behind the slowest upload (and nest
        # the media pipeline's own storage-quota advisory lock inside this one).
        is_postgres = getattr(self._db, "type", "") == "postgres"
        if is_postgres:
            # PostgreSQL READ COMMITTED would let two concurrent INSERT..SELECT
            # statements both see a stale count. Serialise per (server, kind)
            # with an advisory xact lock inside the transaction. The namespace
            # (100/101) stays clear of the invite-quota lock namespaces (1/2);
            # masking to 30 bits keeps the second key in signed int4 range.
            self._db.begin_transaction()
            try:
                self._db.execute(
                    "SELECT pg_advisory_xact_lock(?, ?)",
                    (100 + animated_int, int(server_id) & 0x3FFFFFFF),
                )
            except Exception:
                self._db.rollback()
                raise
        try:
            if not is_postgres:
                self._db.begin_transaction()

            # The UNIQUE(server_id, name) constraint is authoritative for name
            # collisions: a concurrent creator that slipped past the early read
            # is translated to EmojiNameExistsError by the UNIQUE handler in the
            # except chain below. No second fetch_one under the lock is needed.
            now = self._get_timestamp()
            emoji_id = self._generate_id()

            cursor = self._db.execute(
                _ATOMIC_EMOJI_INSERT_SQL,
                (
                    emoji_id,
                    server_id,
                    name,
                    animated_int,
                    url,
                    len(image_data),
                    width,
                    height,
                    user_id,
                    now,
                    server_id,
                    animated_int,
                    max_count,
                ),
            )
            # rowcount 0 means the count guard found the server already at
            # (or over) its cap at insert time — a concurrent creator lost the
            # race. Roll back and surface the same quota error the early check
            # would have raised.
            if getattr(cursor, "rowcount", 0) == 0:
                raise EmojiLimitError(
                    f"Server has reached maximum of {max_count} "
                    f"{'animated emojis' if animated else 'static emojis'}",
                    max_count,
                    max_count,
                )

            self._db.commit()
            # The server's catalog/counts changed and a fresh detail row now
            # exists. Drop only those entries; unrelated servers keep their
            # warm cache entries.
            self._invalidate_emoji_catalog(server_id=server_id, emoji_id=emoji_id)

        except EmojiNameExistsError:
            self._db.rollback()
            raise
        except EmojiLimitError:
            self._db.rollback()
            raise
        except InvalidEmojiFileError:
            self._db.rollback()
            raise
        except Exception as e:
            self._db.rollback()
            if "UNIQUE" in str(e) or "unique" in str(e).lower():
                raise EmojiNameExistsError(
                    f"Emoji with name '{name}' already exists in this server"
                )
            raise

        logger.debug(f"Custom emoji {name} created for server {server_id}")

        result = self.get_custom_emoji(emoji_id)
        assert result is not None
        return result

    def update_custom_emoji(
        self,
        user_id: SnowflakeID,
        emoji_id: SnowflakeID,
        name: Optional[str] = None,
    ) -> CustomEmoji:
        emoji = self.get_custom_emoji(emoji_id)
        if not emoji:
            raise CustomEmojiNotFoundError("Custom emoji not found")

        self._require_manage_server(user_id, emoji.server_id)

        if name is not None:
            name = self._validate_emoji_name(name)

            try:
                self._db.begin_transaction()

                existing = self._db.fetch_one(
                    "SELECT 1 FROM react_custom_emoji WHERE server_id = ? AND name = ? AND id != ?",
                    (emoji.server_id, name, emoji_id),
                )
                if existing:
                    raise EmojiNameExistsError(
                        f"Emoji with name '{name}' already exists in this server"
                    )

                self._db.execute(
                    "UPDATE react_custom_emoji SET name = ? WHERE id = ?",
                    (name, emoji_id),
                )

                self._db.commit()
                # A rename changes both the server list payload and this emoji's
                # detail row. Invalidate before the final read below so it is a
                # fresh fetch rather than the pre-rename cached object.
                self._invalidate_emoji_catalog(
                    server_id=emoji.server_id, emoji_id=emoji_id
                )
            except Exception:
                self._db.rollback()
                raise

        logger.debug(f"Custom emoji {emoji_id} updated")

        result = self.get_custom_emoji(emoji_id)
        assert result is not None
        return result

    def delete_custom_emoji(self, user_id: SnowflakeID, emoji_id: SnowflakeID) -> bool:
        emoji = self.get_custom_emoji(emoji_id)
        if not emoji:
            raise CustomEmojiNotFoundError("Custom emoji not found")

        self._require_manage_server(user_id, emoji.server_id)

        try:
            self._db.begin_transaction()

            self._db.execute(
                "DELETE FROM react_reactions WHERE custom_emoji_id = ?", (emoji_id,)
            )

            self._db.execute("DELETE FROM react_custom_emoji WHERE id = ?", (emoji_id,))

            self._db.commit()
            self._invalidate_emoji_catalog(server_id=emoji.server_id, emoji_id=emoji_id)
        except Exception:
            self._db.rollback()
            raise

        logger.debug(f"Custom emoji {emoji_id} deleted")

        return True

    def get_custom_emoji(self, emoji_id: int) -> Optional[CustomEmoji]:
        cache = self._emoji_catalog_cache()
        key = ("detail", emoji_id)
        cached = cache.get(key, None)
        if cached is not None:
            return replace(cached)

        row = self._db.fetch_one(
            "SELECT * FROM react_custom_emoji WHERE id = ?", (emoji_id,)
        )

        if not row:
            return None

        emoji = self._row_to_custom_emoji(row)
        cache.set(key, emoji)
        return replace(emoji)

    def get_server_custom_emojis(
        self, server_id: int, include_unavailable: bool = False
    ) -> List[CustomEmoji]:
        cache = self._emoji_catalog_cache()
        key = ("list", server_id, bool(include_unavailable))
        cached = cache.get(key, None)
        if cached is not None:
            # Copy the container *and* the mutable CustomEmoji rows: a caller
            # renaming an element of the returned list must not poison the
            # cached catalog for every other viewer.
            return [replace(emoji) for emoji in cached]

        query = """
            SELECT e.*, u.username as uploader_username 
            FROM react_custom_emoji e
            LEFT JOIN auth_users u ON e.created_by = u.id
            WHERE e.server_id = ?
        """

        if not include_unavailable:
            query += " AND e.available = 1"

        query += " ORDER BY e.name"

        rows = self._db.fetch_all(query, (server_id,))

        emojis = tuple(self._row_to_custom_emoji(row) for row in rows)
        cache.set(key, emojis)
        return [replace(emoji) for emoji in emojis]

    def get_emoji_counts(self, server_id: SnowflakeID) -> Dict[str, int]:
        cache = self._emoji_catalog_cache()
        key = ("counts", server_id)
        cached = cache.get(key, None)
        if cached is not None:
            return dict(cached)

        # One grouped COUNT replaces two separate COUNT queries over the same
        # table.  Both quota numbers come from a single index scan on
        # `(server_id, animated)`; a category with no rows is simply absent
        # from the result and falls back to zero below.
        rows = self._db.fetch_all(
            "SELECT animated, COUNT(*) as count FROM react_custom_emoji "
            "WHERE server_id = ? GROUP BY animated",
            (server_id,),
        )
        grouped = {int(row["animated"]): int(row["count"]) for row in rows}
        counts = {
            "static": grouped.get(0, 0),
            "animated": grouped.get(1, 0),
            "max_static": self._config.get("max_emojis_per_server", 50),
            "max_animated": self._config.get("max_animated_emojis_per_server", 50),
        }
        cache.set(key, dict(counts))
        return counts
