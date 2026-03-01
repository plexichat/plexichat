"""
Username Blacklist Manager.
Handles blocked terms, regex patterns, and clever evasion detection.
"""

import re
import unicodedata
from typing import List, Tuple, Optional, Dict, Any
from difflib import SequenceMatcher

from src.core.base import BaseManager

try:
    import re2 as _safe_regex  # pyright: ignore[reportMissingImports]
except ImportError:  # pragma: no cover - fallback when re2 isn't installed
    _safe_regex = re


class BlacklistManager(BaseManager):
    def __init__(self, db):
        super().__init__(db)
        self._cache_key = "username_blacklist_patterns"

    def get_blacklist(self) -> List[Dict[str, Any]]:
        """Get all blacklist entries."""
        # Check cache (RAM cache would be better for performance, but we'll use DB cache if available)
        # For now, fetch from DB.
        return self._db.fetch_all(
            "SELECT * FROM username_blacklist ORDER BY created_at DESC"
        )

    def add_pattern(
        self,
        pattern: str,
        is_regex: bool = False,
        reason: Optional[str] = None,
        admin_id: Optional[int] = None,
    ) -> int:
        """Add a pattern to the blacklist."""
        pattern = pattern.lower().strip()
        if not pattern:
            raise ValueError("Pattern cannot be empty")

        # Basic regex validation using re2 (mandated for ReDoS protection)
        if is_regex:
            try:
                _safe_regex.compile(pattern)
            except Exception as e:
                raise ValueError(f"Invalid regex pattern: {e}")

        try:
            self._db.execute(
                "INSERT INTO username_blacklist (pattern, is_regex, reason, created_by) VALUES (?, ?, ?, ?)",
                (pattern, is_regex, reason, admin_id),
            )
            if hasattr(self._db, "last_insert_id"):
                return self._db.last_insert_id()
            return 0  # Fallback
        except Exception:
            raise ValueError(f"Pattern '{pattern}' already exists")

    def remove_pattern(self, pattern_id: int) -> bool:
        """Remove a pattern from the blacklist."""
        self._db.execute("DELETE FROM username_blacklist WHERE id = ?", (pattern_id,))
        return True

    def is_blocked(
        self, username: str, old_username: Optional[str] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if username is blocked.
        Returns (is_blocked, reason).
        """
        if not username:
            return False, None

        normalized = self._normalize(username)

        # Fetch patterns (should be cached in production)
        patterns = self.get_blacklist()

        for entry in patterns:
            pattern = entry["pattern"]
            is_regex = entry["is_regex"]
            reason = entry["reason"] or "Reserved or prohibited term"

            if is_regex:
                try:
                    # Prefer re2 for ReDoS protection when available.
                    # re2 doesn't support re.IGNORECASE as a flag in search(); use (?i).
                    safe_pattern = f"(?i){pattern}"
                    if _safe_regex.search(safe_pattern, username) or _safe_regex.search(
                        safe_pattern, normalized
                    ):
                        return True, reason
                except Exception:
                    continue  # Skip invalid regex
            else:
                # Exact-match only (case-insensitive), including normalized matching
                pat_norm = self._normalize(pattern)
                if pat_norm and pat_norm == normalized:
                    return True, reason
                if pattern == username.lower().strip():
                    return True, reason

        # Similarity check if old_username is provided (for forced changes)
        if old_username:
            old_norm = self._normalize(old_username)
            # 1. Sequence similarity (SequenceMatcher)
            seq_similarity = SequenceMatcher(None, normalized, old_norm).ratio()

            # 2. Character set similarity (catch anagrams/rearrangements)
            new_sorted = "".join(sorted(normalized))
            old_sorted = "".join(sorted(old_norm))
            charset_similarity = SequenceMatcher(None, new_sorted, old_sorted).ratio()

            # Use the higher of the two
            max_similarity = max(seq_similarity, charset_similarity)

            if max_similarity > 0.8:  # 80% similarity
                return True, "New username is too similar to the old one"

        return False, None

    def _normalize(self, text: str) -> str:
        """
        Normalize text to handle leetspeak and confusables.
        """
        if not text:
            return ""

        # 1. Lowercase
        text = text.lower()

        # 2. NFKD normalization (separate accents)
        text = unicodedata.normalize("NFKD", text)

        # 3. Custom leetspeak mapping
        confusables = {
            "@": "a",
            "4": "a",
            "8": "b",
            "(": "c",
            "3": "e",
            "6": "g",
            "1": "i",
            "!": "i",
            "|": "i",
            "0": "o",
            "$": "s",
            "5": "s",
            "7": "t",
            "+": "t",
            "\\": "v",
            "vv": "w",
            "2": "z",
        }

        result = []
        for char in text:
            result.append(confusables.get(char, char))

        # 4. Remove non-alphanumeric to catch "b.a.d"
        text = "".join(result)
        # Internal static regex is safe with standard re
        text = re.sub(r"[^a-z0-9]", "", text)

        return text
