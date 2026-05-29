import re

from src.core.base import SnowflakeID
from ..exceptions import (
    InvalidEmojiError,
    InvalidEmojiNameError,
)
from .protocol import ReactionProtocol


CUSTOM_EMOJI_PATTERN = re.compile(r"^<a?:([a-zA-Z0-9_]+):(\d+)>$")


class ReactionValidationMixin(ReactionProtocol):
    def _validate_emoji(self, emoji: str) -> tuple:
        if not emoji or not emoji.strip():
            raise InvalidEmojiError("Emoji cannot be empty")

        emoji = emoji.strip()

        custom_match = CUSTOM_EMOJI_PATTERN.match(emoji)
        if custom_match:
            emoji_id = int(custom_match.group(2))
            return (True, emoji_id, emoji)

        if emoji.startswith(":") or emoji.startswith("<"):
            raise InvalidEmojiError("Invalid emoji format")

        if len(emoji) > 32:
            raise InvalidEmojiError("Emoji is too long")

        if not self._is_valid_unicode_emoji(emoji):
            raise InvalidEmojiError("Invalid emoji characters")

        return (False, None, emoji)

    def _is_valid_unicode_emoji(self, text: str) -> bool:
        VALID_EMOJI_RANGES = [
            (0x1F000, 0x1FAFF),
            (0x1FB00, 0x1FBFF),
            (0x2600, 0x26FF),
            (0x2700, 0x27BF),
            (0x2300, 0x23FF),
            (0x2B50, 0x2B55),
            (0xFE00, 0xFE0F),
            (0xE0020, 0xE007F),
        ]

        REJECTED_CHARS = {
            0x200B,
            0x200C,
            0x200D,
            0x202E,
            0x202D,
            0xFEFF,
        }

        if not text:
            return False

        zwj_count = 0
        for char in text:
            code = ord(char)

            if code < 0x20 and code not in (0x09, 0x0A, 0x0D):
                return False

            if code == 0x200D:
                zwj_count += 1
                if zwj_count > 3:
                    return False
                continue

            if code in REJECTED_CHARS:
                return False

            in_valid_range = False
            for start, end in VALID_EMOJI_RANGES:
                if start <= code <= end:
                    in_valid_range = True
                    break

            if (
                (0x30 <= code <= 0x39)
                or (0x41 <= code <= 0x5A)
                or (0x61 <= code <= 0x7A)
            ):
                in_valid_range = True

            if code in (0x3A, 0x2D, 0x5F):
                in_valid_range = True

            if code in (0x20E3, 0xA9, 0xAE):
                in_valid_range = True

            if not in_valid_range:
                return False

        return True

    def _validate_custom_emoji_for_server(
        self, custom_emoji_id: SnowflakeID, server_id: SnowflakeID
    ) -> bool:
        row = self._db.fetch_one(
            "SELECT 1 FROM react_custom_emoji WHERE id = ? AND server_id = ? AND available = 1",
            (custom_emoji_id, server_id),
        )
        return row is not None

    def _validate_emoji_name(self, name: str) -> str:
        if not name or not name.strip():
            raise InvalidEmojiNameError("Emoji name cannot be empty")

        name = name.strip().lower()
        min_len = self._config.get("emoji_min_name_length", 2)
        max_len = self._config.get("emoji_max_name_length", 32)

        if len(name) < min_len or len(name) > max_len:
            raise InvalidEmojiNameError(
                f"Emoji name must be {min_len}-{max_len} characters"
            )

        if not re.match(r"^[a-z0-9_]+$", name):
            raise InvalidEmojiNameError(
                "Emoji name can only contain lowercase letters, numbers, and underscores"
            )

        return name
