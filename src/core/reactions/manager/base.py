import functools
from typing import Optional, Dict, Any
import utils.config as config
import utils.logger as logger
from src.core.base import BaseManager, SnowflakeID
from ..models import (
    Reaction,
    CustomEmoji,
)
from .permissions import ReactionPermissionsMixin
from .validation import ReactionValidationMixin
from .reactions import ReactionOpsMixin
from .emojis import EmojiOpsMixin
from .cache import (
    ReactionReadCache,
    _MISS,
    DEFAULT_READ_TTL_SECONDS,
    DEFAULT_MAX_ENTRIES,
)


class ReactionBase(BaseManager):
    # NOTE: ``_migrate_emoji_table`` is intentionally NOT declared here. The
    # real implementation lives in EmojiOpsMixin; a stub in this class would
    # shadow it via C3 MRO (ReactionBase precedes EmojiOpsMixin in
    # ReactionManager's bases), silently disabling the legacy-table migration.

    def __init__(
        self,
        db,
        auth_module=None,
        messaging_module=None,
        servers_module=None,
        relationships_module=None,
        media_module=None,
    ):
        super().__init__(db, auth_module)
        self._messaging = messaging_module
        self._servers = servers_module
        self._relationships = relationships_module
        self._media = media_module
        self._config = self._load_config()
        # L0 aggregation cache.  Bounded and short-lived; it memoizes only the
        # expensive *query results* (never the authorization gate).  The knobs
        # are config-driven so a deployment can shrink or disable it (ttl=0)
        # without a code change; defaults are deliberately conservative.
        self._reaction_read_cache = ReactionReadCache(
            ttl_seconds=float(
                self._config.get("reaction_read_cache_ttl", DEFAULT_READ_TTL_SECONDS)
            ),
            max_entries=int(
                self._config.get("reaction_read_cache_max_entries", DEFAULT_MAX_ENTRIES)
            ),
        )
        self._wire_relationship_cache_invalidation()
        self._wire_emoji_cache_invalidation()
        # M2: the custom-emoji schema migration was DEFINED but never invoked,
        # so a table created before the url/size/width/height/created_by/
        # available columns existed never gained them, and every INSERT with
        # the new columns failed at runtime. Run it once at construction; it
        # is idempotent (per-column ALTERs are individually caught) and
        # safe on fresh installs (missing table short-circuits).
        try:
            # Resolved via getattr so static analysis of ReactionBase stays
            # clean while the EmojiOpsMixin implementation (the only real
            # one) is what actually runs at runtime.
            getattr(self, "_migrate_emoji_table")()
        except Exception as e:
            logger.warning(f"Failed to migrate react_custom_emoji table: {e}")
        logger.info("Reaction module initialized")

    def _load_config(self) -> Dict[str, Any]:
        defaults = {
            "max_reactions_per_message": 20,
            "max_users_per_reaction_page": 100,
            "max_emojis_per_server": 50,
            "max_animated_emojis_per_server": 50,
            "max_emoji_size": 262144,
            "emoji_allowed_formats": ["image/png", "image/gif", "image/webp"],
            "emoji_max_name_length": 32,
            "emoji_min_name_length": 2,
        }
        reactions_config = config.get("reactions", {})
        emojis_config = config.get("emojis", {})
        defaults.update(
            {
                "reaction_read_cache_ttl": DEFAULT_READ_TTL_SECONDS,
                "reaction_read_cache_max_entries": DEFAULT_MAX_ENTRIES,
            }
        )
        return {**defaults, **reactions_config, **emojis_config}

    def _cached_reaction_read(self, key, loader):
        """Return a cached aggregation result, computing and storing on miss.

        ``loader`` runs only on a miss.  Callers MUST perform their live authz
        gate before calling this helper -- the cache holds query results only,
        so a revoked viewer is still denied on the next request.
        """
        cached = self._reaction_read_cache.get(key)
        if cached is not _MISS:
            return cached
        value = loader()
        self._reaction_read_cache.set(key, value)
        return value

    def _invalidate_reaction_reads(self) -> None:
        """Drop every cached aggregation result after a reaction write."""
        self._reaction_read_cache.clear()

    def _wire_relationship_cache_invalidation(self) -> None:
        """Invalidate viewer-scoped reads when a block/unblock changes them.

        Reaction counts, reactor lists and batch pills all exclude the viewer's
        blocked users, so a relationship change invalidates those entries just
        as a reaction write does.  The relationships module is a dependency we
        do not own here, so register a small hook on the wired instance instead
        of editing that module; all registration lives in this file.
        """
        rel = self._relationships
        if rel is None:
            return
        try:
            hooks = getattr(rel, "_reaction_read_invalidators", None)
            if hooks is None:
                hooks = []
                setattr(rel, "_reaction_read_invalidators", hooks)

                def _wrap(name: str) -> None:
                    original = getattr(rel, name, None)
                    if not callable(original):
                        return

                    @functools.wraps(original)
                    def _invalidating(*args, **kwargs):
                        try:
                            return original(*args, **kwargs)
                        finally:
                            for hook in list(hooks):
                                hook()

                    setattr(rel, name, _invalidating)

                _wrap("block_user")
                _wrap("unblock_user")

            hook = self._invalidate_reaction_reads
            if hook not in hooks:
                hooks.append(hook)
        except Exception as e:
            logger.debug(f"Could not wire relationship cache invalidation: {e}")

    def _wire_emoji_cache_invalidation(self) -> None:
        """Invalidate cached reaction pills after custom-emoji mutations.

        Custom emoji are LEFT JOINed into reaction pills (they supply the url),
        and deleting one also deletes every reaction that used it.  Wrapping the
        manager's own mutation methods here keeps the invalidation inside this
        file instead of editing the emoji mixin, which may be owned by a
        parallel change.
        """
        for name in (
            "create_custom_emoji",
            "update_custom_emoji",
            "delete_custom_emoji",
        ):
            try:
                original = getattr(self, name)
                if not callable(original) or getattr(
                    original, "_reaction_read_hook", False
                ):
                    continue

                @functools.wraps(original)
                def _invalidating(*args, __original=original, **kwargs):
                    try:
                        return __original(*args, **kwargs)
                    finally:
                        self._invalidate_reaction_reads()

                setattr(_invalidating, "_reaction_read_hook", True)
                setattr(self, name, _invalidating)
            except Exception as e:
                logger.debug(f"Could not wire emoji cache invalidation: {e}")

    def _get_message(self, message_id: SnowflakeID) -> Optional[Dict]:
        return self._db.fetch_one(
            "SELECT * FROM msg_messages WHERE id = ? AND deleted = 0", (message_id,)
        )

    def _get_conversation(self, conversation_id: SnowflakeID) -> Optional[Dict]:
        return self._db.fetch_one(
            "SELECT * FROM msg_conversations WHERE id = ? AND deleted = 0",
            (conversation_id,),
        )

    def _get_unique_emoji_count(self, message_id: SnowflakeID) -> int:
        row = self._db.fetch_one(
            "SELECT COUNT(DISTINCT emoji) as count FROM react_reactions WHERE message_id = ?",
            (message_id,),
        )
        return row["count"] if row else 0

    def _is_unique_violation(self, e: Exception) -> bool:
        if type(e).__name__ == "IntegrityError":
            return True
        message = str(e).lower()
        return "unique constraint" in message or "duplicate entry" in message

    def _row_to_reaction(self, row) -> Reaction:
        return Reaction(
            id=row["id"],
            message_id=row["message_id"],
            user_id=row["user_id"],
            emoji=row["emoji"],
            is_custom=bool(row["is_custom"]),
            custom_emoji_id=row["custom_emoji_id"],
            created_at=row["created_at"],
        )

    def _row_to_custom_emoji(self, row) -> CustomEmoji:
        return CustomEmoji(
            id=row["id"],
            server_id=row["server_id"],
            name=row["name"],
            animated=bool(row["animated"]),
            url=row.get("url", "") or "",
            size=row.get("size", 0) or 0,
            width=row.get("width"),
            height=row.get("height"),
            created_by=row.get("created_by", 0) or 0,
            available=bool(row.get("available", 1)),
            created_at=row["created_at"],
            uploader_username=row.get("uploader_username"),
        )


class ReactionManager(
    ReactionBase,
    ReactionPermissionsMixin,
    ReactionValidationMixin,
    ReactionOpsMixin,
    EmojiOpsMixin,
):
    pass
