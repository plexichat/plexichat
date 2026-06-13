from typing import Optional, List, Dict
from io import BytesIO
from PIL import Image
import utils.logger as logger
from src.core.base import SnowflakeID
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


class EmojiOpsMixin(ReactionProtocol):
    def _migrate_emoji_table(self):
        try:
            self._db.execute("SELECT url FROM react_custom_emoji LIMIT 1")
        except Exception:
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

    def _check_emoji_limits(self, server_id: SnowflakeID, animated: bool) -> None:
        if animated:
            max_count = self._config.get("max_animated_emojis_per_server", 50)
            row = self._db.fetch_one(
                "SELECT COUNT(*) as count FROM react_custom_emoji WHERE server_id = ? AND animated = 1",
                (server_id,),
            )
        else:
            max_count = self._config.get("max_emojis_per_server", 50)
            row = self._db.fetch_one(
                "SELECT COUNT(*) as count FROM react_custom_emoji WHERE server_id = ? AND animated = 0",
                (server_id,),
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
        if self._servers:
            if not self._servers.has_permission(user_id, server_id, "server.manage"):
                raise PermissionDeniedError(
                    "Missing permission to manage server", "server.manage"
                )

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

        self._check_emoji_limits(server_id, animated)

        try:
            self._db.begin_transaction()

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

            now = self._get_timestamp()
            emoji_id = self._generate_id()

            self._db.execute(
                """INSERT INTO react_custom_emoji 
                   (id, server_id, name, animated, url, size, width, height, created_by, available, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                (
                    emoji_id,
                    server_id,
                    name,
                    1 if animated else 0,
                    url,
                    len(image_data),
                    width,
                    height,
                    user_id,
                    now,
                ),
            )

            self._db.commit()

        except EmojiNameExistsError:
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

        if self._servers:
            if not self._servers.has_permission(
                user_id, emoji.server_id, "server.manage"
            ):
                raise PermissionDeniedError(
                    "Missing permission to manage server", "server.manage"
                )

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

        if self._servers:
            if not self._servers.has_permission(
                user_id, emoji.server_id, "server.manage"
            ):
                raise PermissionDeniedError(
                    "Missing permission to manage server", "server.manage"
                )

        try:
            self._db.begin_transaction()

            self._db.execute(
                "DELETE FROM react_reactions WHERE custom_emoji_id = ?", (emoji_id,)
            )

            self._db.execute("DELETE FROM react_custom_emoji WHERE id = ?", (emoji_id,))

            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

        logger.debug(f"Custom emoji {emoji_id} deleted")

        return True

    def get_custom_emoji(self, emoji_id: int) -> Optional[CustomEmoji]:
        row = self._db.fetch_one(
            "SELECT * FROM react_custom_emoji WHERE id = ?", (emoji_id,)
        )

        if not row:
            return None

        return self._row_to_custom_emoji(row)

    def get_server_custom_emojis(
        self, server_id: int, include_unavailable: bool = False
    ) -> List[CustomEmoji]:
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

        return [self._row_to_custom_emoji(row) for row in rows]

    def get_emoji_counts(self, server_id: SnowflakeID) -> Dict[str, int]:
        static_row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM react_custom_emoji WHERE server_id = ? AND animated = 0",
            (server_id,),
        )
        animated_row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM react_custom_emoji WHERE server_id = ? AND animated = 1",
            (server_id,),
        )
        return {
            "static": static_row["count"] if static_row else 0,
            "animated": animated_row["count"] if animated_row else 0,
            "max_static": self._config.get("max_emojis_per_server", 50),
            "max_animated": self._config.get("max_animated_emojis_per_server", 50),
        }
