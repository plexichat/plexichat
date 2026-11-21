import re

from src.core.base import SnowflakeID
from src.core.security.hardening import HardeningViolation, parse_id
from ..exceptions import (
    InvalidEmojiError,
    InvalidEmojiNameError,
)
from .bounds import MAX_REACTION_EMOJI_LENGTH
from .protocol import ReactionProtocol


CUSTOM_EMOJI_PATTERN = re.compile(r"^<a?:([a-zA-Z0-9_]+):(\d+)>$")
SHORTCODE_EMOJI_PATTERN = re.compile(r"^:([a-zA-Z0-9_]{1,32}):$")
KEYCAP_EMOJI_PATTERN = re.compile(r"^[#*0-9]\uFE0F?\u20E3$")
ASCII_WORD_PATTERN = re.compile(r"[A-Za-z0-9_]+")

# These tables are immutable and are consulted on every unicode-emoji reaction
# (add/remove).  Building the list/sets and the per-character ``any()`` generator
# inside the hot method made every validation allocate ~a dozen short-lived
# objects.  Hoisting them to module scope removes that steady-state churn.
VALID_EMOJI_RANGES = (
    (0x1F000, 0x1FAFF),
    (0x1FB00, 0x1FBFF),
    (0x2600, 0x26FF),
    (0x2700, 0x27BF),
    (0x2300, 0x23FF),
    (0x2B00, 0x2BFF),
    (0xFE00, 0xFE0F),
    (0xE0020, 0xE007F),
)
SINGLETON_CHARS = frozenset({0x20E3, 0xA9, 0xAE})
REJECTED_CHARS = frozenset({0x200B, 0x200C, 0x202E, 0x202D, 0xFEFF})


class ReactionValidationMixin(ReactionProtocol):
    def _validate_emoji(self, emoji: str) -> tuple:
        if not emoji or not emoji.strip():
            raise InvalidEmojiError("Emoji cannot be empty")

        emoji = emoji.strip()

        custom_match = CUSTOM_EMOJI_PATTERN.match(emoji)
        if custom_match:
            # Bound the name even though the route guard caps the whole token:
            # this validator is also a public module API reachable without the
            # HTTP layer, and an unbounded name is stored verbatim in the
            # reaction row and echoed in WebSocket events.
            name = custom_match.group(1)
            if len(name) > MAX_REACTION_EMOJI_LENGTH:
                raise InvalidEmojiError("Custom emoji name is too long")

            # ``int()`` accepts an arbitrarily long digit string until Python's
            # integer-conversion cap (4300 digits) raises an uncaught ValueError
            # -- a small malformed token could crash the worker with a 500. It
            # also accepts ids far outside the snowflake range, which later
            # overflow SQLite's 64-bit INTEGER column. Parse through the shared
            # range-checked primitive so both cases become a normal validation
            # error instead.
            try:
                emoji_id = parse_id(custom_match.group(2), "custom_emoji_id")
            except HardeningViolation as exc:
                raise InvalidEmojiError(f"Invalid custom emoji id: {exc}") from exc
            return (True, emoji_id, emoji)

        if len(emoji) > MAX_REACTION_EMOJI_LENGTH:
            raise InvalidEmojiError("Emoji is too long")

        # Legacy ":shortcode:" reactions remain valid.
        if SHORTCODE_EMOJI_PATTERN.match(emoji):
            return (False, None, emoji)

        if emoji.startswith(":") or emoji.startswith("<"):
            raise InvalidEmojiError("Invalid emoji format")

        if not self._is_valid_unicode_emoji(emoji):
            raise InvalidEmojiError("Invalid emoji characters")

        return (False, None, emoji)

    def _is_valid_unicode_emoji(self, text: str) -> bool:
        if not text:
            return False

        # Keycap sequences ("1️⃣", "#️⃣", "*️⃣") are the only place ASCII
        # digits may appear inside a unicode emoji.
        if KEYCAP_EMOJI_PATTERN.match(text):
            return True

        # Fail closed (M1): plain ASCII words are never emoji. The range
        # check below already rejects them, but reject explicitly so a
        # future range edit cannot reopen arbitrary-text reactions.
        if ASCII_WORD_PATTERN.fullmatch(text):
            return False

        zwj_count = 0
        for char in text:
            code = ord(char)

            if code < 0x20:
                return False

            if code == 0x200D:
                zwj_count += 1
                if zwj_count > 3:
                    return False
                continue

            if code in REJECTED_CHARS:
                return False

            # Inline the range scan instead of ``any(generator)``: the latter
            # allocates a fresh generator object for every character on this
            # hot path.  Iterate the module-level tuple directly.
            in_valid_range = False
            for start, end in VALID_EMOJI_RANGES:
                if start <= code <= end:
                    in_valid_range = True
                    break

            if code in SINGLETON_CHARS:
                in_valid_range = True

            if not in_valid_range:
                return False

        return True

    def _validate_custom_emoji_exists(self, custom_emoji_id: SnowflakeID) -> bool:
        row = self._db.fetch_one(
            "SELECT 1 FROM react_custom_emoji WHERE id = ? AND available = 1",
            (custom_emoji_id,),
        )
        return row is not None

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
