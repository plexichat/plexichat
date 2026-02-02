"""
Username Blacklist Manager.
Handles blocked terms, regex patterns, and clever evasion detection.
"""
import re
import unicodedata
from typing import List, Tuple, Optional, Dict, Any
from difflib import SequenceMatcher

from src.core.base import BaseManager

class BlacklistManager(BaseManager):
    def __init__(self, db):
        super().__init__(db)
        self._cache_key = "username_blacklist_patterns"

    def get_blacklist(self) -> List[Dict[str, Any]]:
        """Get all blacklist entries."""
        # Check cache (RAM cache would be better for performance, but we'll use DB cache if available)
        # For now, fetch from DB.
        return self._db.fetch_all("SELECT * FROM username_blacklist ORDER BY created_at DESC")

    def add_pattern(self, pattern: str, is_regex: bool = False, reason: str = None, admin_id: int = None) -> int:
        """Add a pattern to the blacklist."""
        pattern = pattern.lower().strip()
        if not pattern:
            raise ValueError("Pattern cannot be empty")
            
        try:
            self._db.execute(
                "INSERT INTO username_blacklist (pattern, is_regex, reason, created_by) VALUES (?, ?, ?, ?)",
                (pattern, is_regex, reason, admin_id)
            )
            # In SQLite, last_insert_rowid is usually fetched via a separate call or specific method
            # BaseManager or Database usually has last_insert_id or similar
            if hasattr(self._db, "last_insert_id"):
                return self._db.last_insert_id()
            return 0 # Fallback
        except Exception:
            raise ValueError(f"Pattern '{pattern}' already exists")

    def remove_pattern(self, pattern_id: int) -> bool:
        """Remove a pattern from the blacklist."""
        self._db.execute("DELETE FROM username_blacklist WHERE id = ?", (pattern_id,))
        return True

    def is_blocked(self, username: str, old_username: str = None) -> Tuple[bool, Optional[str]]:
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
                    if re.search(pattern, username, re.IGNORECASE) or re.search(pattern, normalized, re.IGNORECASE):
                        return True, reason
                except re.error:
                    continue # Skip invalid regex
            else:
                # Substring check in normalized string
                if pattern in normalized:
                    return True, reason
                # Also check exact match in original (case-insensitive)
                if pattern == username.lower():
                    return True, reason

        # Similarity check if old_username is provided (for forced changes)
        if old_username:
            old_norm = self._normalize(old_username)
            # Only check similarity if the old username was actually "bad" (which implies we are forcing change)
            # But here we just check if new is similar to old.
            # If the user is forced to change, they shouldn't pick something similar.
            similarity = SequenceMatcher(None, normalized, old_norm).ratio()
            if similarity > 0.8: # 80% similarity
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
        text = unicodedata.normalize('NFKD', text)
        
        # 3. Custom leetspeak mapping
        confusables = {
            '@': 'a', '4': 'a',
            '8': 'b',
            '(': 'c',
            '3': 'e',
            '6': 'g',
            '1': 'i', '!': 'i', '|': 'i',
            '0': 'o',
            '$': 's', '5': 's',
            '7': 't', '+': 't',
            '\\': 'v',
            'vv': 'w',
            '2': 'z'
        }
        
        result = []
        for char in text:
            result.append(confusables.get(char, char))
            
        # 4. Remove non-alphanumeric to catch "b.a.d"
        text = "".join(result)
        text = re.sub(r'[^a-z0-9]', '', text)
        
        return text
