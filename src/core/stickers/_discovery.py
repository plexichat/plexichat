"""Usage tracking, discovery, suggestions, and row converters for stickers.

Split out of ``manager.py`` to keep every module under 1000 lines; these
methods are mixed into :class:`~src.core.stickers.manager.StickerManager`.
"""

import json
import re
from typing import Any, Optional, List

import utils.logger as logger

from src.core.security.hardening import HardeningViolation, bounded_int

from .models import (
    StickerPack,
    Sticker,
    StickerUsage,
    StickerSuggestion,
    StickerFormat,
    PackType,
)
from .exceptions import (
    StickerNotFoundError,
    MessageNotFoundError,
    PermissionDeniedError,
)


# Hard, non-overridable ceiling for discovery/list pages at the core boundary.
# The HTTP layer validates ``limit`` with Pydantic (e.g. sticker suggestions is
# ``ge=1, le=20``), but these manager methods are public internal APIs called
# directly by other modules and by tests that bypass those schemas. A limit
# routed straight into SQL ``LIMIT ?`` is dangerous in both directions:
# ``LIMIT -1`` means *unlimited* in SQLite, and a huge value forces the whole
# result set to be materialised. Both directions are bounded here so the
# invariant holds regardless of which caller reaches the manager.
MAX_DISCOVERY_LIMIT = 200


def _bounded_listing_limit(value: Any, default: int = 10) -> int:
    """Coerce a caller-supplied page limit into ``[0, MAX_DISCOVERY_LIMIT]``.

    ``bounded_int`` rejects bools, fractions, Unicode digit homoglyphs and
    absurd integers, so type confusion cannot smuggle an unbounded value
    through. Negative / oversized / unparsable limits fail closed to an empty
    page (``LIMIT 0``), preserving the historical negative-limit behaviour
    asserted by the regression tests while removing the unbounded case.
    """
    if value is None:
        return default
    try:
        number = bounded_int(value, "limit", minimum=-(2**63), maximum=2**63 - 1)
    except HardeningViolation:
        return 0
    return max(0, min(number, MAX_DISCOVERY_LIMIT))


# Attributes/methods provided by the composing StickerManager. Declared as a
# real (empty) base class so static checkers see the contract on the mixin;
# it contributes no runtime behaviour of its own.
class _ManagerBase:
    _db: Any
    _messaging: Optional[Any]
    _servers: Optional[Any]
    _config: dict
    _encrypt_descriptions: bool

    def _get_timestamp(self) -> int:
        raise NotImplementedError

    def _generate_id(self) -> int:
        raise NotImplementedError

    def get_sticker(self, sticker_id: int) -> Optional[Sticker]:
        raise NotImplementedError

    def get_pack(self, pack_id: int, user_id: Optional[int] = None):
        raise NotImplementedError

    def get_popular_stickers(self, limit: int = 10) -> List[Sticker]:
        raise NotImplementedError

    def _is_server_member(self, user_id: int, server_id: int) -> bool:
        raise NotImplementedError

    def _row_to_sticker(self, row) -> Sticker:
        raise NotImplementedError


class StickerUsageDiscoveryMixin(_ManagerBase):
    """Usage recording, discovery/search, suggestions, row conversion."""

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
        # Bounded at the core boundary (see _bounded_listing_limit): callers
        # that bypass the API schema cannot turn this into an unbounded read.
        limit = _bounded_listing_limit(limit)
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
        # Bounded at the core boundary (see _bounded_listing_limit).
        limit = _bounded_listing_limit(limit)
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
        # Bounded at the core boundary (see _bounded_listing_limit).
        limit = _bounded_listing_limit(limit)
        # Escape LIKE metacharacters so ``%``/``_``/``\`` are matched
        # literally instead of acting as wildcards (docs/admin-hardening.md).
        escaped = (
            query.strip()
            .lower()
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        needle = f"%{escaped}%"
        rows = self._db.fetch_all(
            """SELECT s.*, COUNT(u.id) as usage_count
               FROM sticker_stickers s
               LEFT JOIN sticker_usage u ON s.id = u.sticker_id
               WHERE LOWER(s.name) LIKE ? ESCAPE '\\'
                  OR LOWER(s.tags) LIKE ? ESCAPE '\\'
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

    def _sticker_pack_id(self, sticker_id: int) -> Optional[int]:
        """Look up only a sticker's pack id, skipping the usage-count JOIN.

        ``get_sticker`` aggregates ``sticker_usage`` with ``COUNT(u.id)`` to
        build ``Sticker.usage_count``. Permission checks and the send/existence
        gate need only the sticker's identity/pack, so paying for that
        aggregation on every send is wasted work on the usage hot path.
        """
        row = self._db.fetch_one(
            "SELECT pack_id FROM sticker_stickers WHERE id = ?", (sticker_id,)
        )
        return int(row["pack_id"]) if row else None

    def _pack_authz(self, pack_id: int):
        """Return ``(pack_type, server_id, created_by)`` without the count JOIN.

        ``get_pack`` aggregates ``COUNT(s.id)`` for ``StickerPack.sticker_count``
        and (for SERVER packs) probes membership. Authorization only needs the
        three authz-relevant columns; membership is checked once explicitly by
        the caller instead of twice (once here, once in ``get_pack``).
        """
        row = self._db.fetch_one(
            "SELECT pack_type, server_id, created_by FROM sticker_packs WHERE id = ?",
            (pack_id,),
        )
        if not row:
            return None
        try:
            pack_type = PackType(row["pack_type"])
        except ValueError:
            # Unknown/corrupt pack type cannot be authorized. Fail closed.
            return None
        return pack_type, row["server_id"], row["created_by"]

    def can_use_sticker(self, user_id: int, sticker_id: int) -> bool:
        """Permission query used by message senders.

        Server packs (pack_type=SERVER, server_id != NULL) require
        membership; "personal" packs (``PackType.PERSONAL``) and server packs
        without a server require ownership of the pack
        (``created_by == user_id``); default and purchased packs are open to
        everyone.
        """
        pack_id = self._sticker_pack_id(sticker_id)
        if pack_id is None:
            return False
        authz = self._pack_authz(pack_id)
        if authz is None:
            return False
        pack_type, server_id, created_by = authz
        if pack_type in (PackType.DEFAULT, PackType.PURCHASED):
            return True
        if pack_type == PackType.SERVER and server_id:
            return self._is_server_member(user_id, server_id)
        # SERVER (no server) and PERSONAL packs require ownership.
        return created_by == user_id

    def send_sticker(
        self, user_id: int, message_id: int, sticker_id: int
    ) -> StickerUsage:
        # Only the sticker's existence is needed here (the converted sticker
        # object is never used below). Use the lightweight lookup instead of
        # ``get_sticker``'s usage-count aggregation.
        if self._sticker_pack_id(sticker_id) is None:
            raise StickerNotFoundError("Sticker not found")
        if self._messaging:
            msg = self._messaging.get_message(user_id, message_id)
            if not msg:
                raise MessageNotFoundError("Message not found")
            # Sticker usage must be attributed only to the caller's own
            # messages. ``get_message`` only verifies the caller is a
            # participant of the conversation, so a participant could otherwise
            # attach usage stats to another user's message.
            if getattr(msg, "author_id", None) != user_id:
                raise PermissionDeniedError(
                    "Sticker usage can only be attached to your own messages"
                )
        else:
            # Fallback: when the messaging module is unavailable, verify the
            # message exists and belongs to this user directly so usage stats
            # cannot be associated with arbitrary messages.
            row = self._db.fetch_one(
                "SELECT 1 FROM msg_messages WHERE id = ? AND author_id = ?",
                (message_id, user_id),
            )
            if not row:
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
        # Only surface a server's stickers to members of that server. Without
        # this check any authenticated user could enumerate another server's
        # sticker pack by simply passing its server_id. When the servers
        # module is unwired we cannot verify membership, so the caller's
        # requested server scope is DENIED (no server packs enumerated).
        if server_id is not None:
            if self._servers is None:
                logger.error(
                    "Sticker suggestion enumeration failed CLOSED: servers "
                    "module is not wired; denying server-scoped suggestions."
                )
                return []
            if not self._is_server_member(user_id, server_id):
                server_id = None
        # Only fall back to config when the caller supplied no limit at all;
        # ``limit or ...`` used to treat an explicit 0 as "use default". Route
        # the resolved value through the same core bound so a direct caller
        # cannot bypass the API schema's ``le=20`` with an oversized value.
        if limit is None:
            max_suggestions = self._config.get("max_suggestions", 10)
        else:
            max_suggestions = limit
        max_suggestions = _bounded_listing_limit(max_suggestions, default=10)
        content_lower = content.lower()
        words = set(re.findall(r"\w+", content_lower))
        suggestions = []
        rows = self._db.fetch_all(
            "SELECT s.*, COUNT(u.id) as usage_count FROM sticker_stickers s "
            "INNER JOIN sticker_packs p ON s.pack_id = p.id "
            "LEFT JOIN sticker_usage u ON s.id = u.sticker_id "
            "WHERE (p.pack_type = ? OR (p.pack_type = ? AND p.server_id = ?) "
            "   OR (p.pack_type = ? AND p.created_by = ?)) "
            "GROUP BY s.id",
            (
                PackType.DEFAULT.value,
                PackType.SERVER.value,
                server_id or 0,
                PackType.PERSONAL.value,
                user_id,
            ),
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
            description=row.get("description"),
            tags=tags,
            related_emoji=row["related_emoji"],
            url=row["url"],
            size=row["size"],
            width=row["width"],
            height=row["height"],
            created_at=row["created_at"],
            usage_count=usage_count,
        )
