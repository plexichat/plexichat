"""
Username Blacklist Manager.
Handles blocked terms, regex patterns, and clever evasion detection.
"""

import importlib
import re
import threading
import time
import unicodedata
import weakref
from typing import Any, Dict, List, NamedTuple, Optional, Tuple, cast
from difflib import SequenceMatcher

from src.core.base import BaseManager

try:
    _safe_regex = cast(Any, importlib.import_module("re2"))
except ImportError:  # pragma: no cover - fallback when re2 isn't installed
    _safe_regex = re


# ``is_blocked`` sits on the registration and profile-update paths. Before this
# plan cache it re-read the whole blacklist table and re-normalised every
# literal pattern on every call, so a single username check cost one DB round
# trip plus O(patterns) Unicode normalisations. The derived plan depends only
# on the table contents, so it can be built once and reused.
#
# The table is shared and can be written from another process (the admin routes
# insert/delete with a raw DB handle), so the cached plan is deliberately
# short-lived and process-local: every node rebuilds from the authoritative
# table within at most this window, and no node can keep a stale "not blocked"
# verdict indefinitely. Local writes invalidate immediately. A larger table
# than the cap is simply never cached — correctness never depends on the cache.
_PLAN_TTL_SECONDS = 1.0
_MAX_CACHED_PATTERNS = 1024

# The admin routes write the table with a raw DB handle rather than through
# this manager. Track live managers weakly so those writes can invalidate this
# process's plans immediately; other nodes still converge via the TTL above.
_MANAGERS: "weakref.WeakSet[BlacklistManager]" = weakref.WeakSet()
_MANAGERS_LOCK = threading.Lock()


def invalidate_all_plans() -> None:
    """Drop every live local plan (call after a blacklist table write)."""
    with _MANAGERS_LOCK:
        managers = list(_MANAGERS)
    for manager in managers:
        manager.invalidate_plan()


class _PreparedPattern(NamedTuple):
    """One blacklist row with its per-check work done once."""

    is_regex: bool
    compiled: Any  # compiled pattern, or None when the stored regex is invalid
    normalized: str  # pre-normalised literal
    raw: str  # lower/stripped literal
    reason: str


class _Plan(NamedTuple):
    patterns: Tuple[_PreparedPattern, ...]
    built_at: float


class BlacklistManager(BaseManager):
    def __init__(self, db):
        super().__init__(db)
        self._cache_key = "username_blacklist_patterns"
        # One slot per manager instance: bounded by construction, never keyed
        # by request-derived data (so no per-user growth or cross-user leak).
        self._plan: Optional[_Plan] = None
        self._plan_lock = threading.Lock()
        with _MANAGERS_LOCK:
            _MANAGERS.add(self)

    def invalidate_plan(self) -> None:
        """Drop the derived plan so the next check reads the table again."""
        with self._plan_lock:
            self._plan = None

    def get_blacklist(self) -> List[Dict[str, Any]]:
        """Get all blacklist entries from the authoritative table.

        Callers that check usernames use the derived plan instead; this method
        is the single DB read behind a plan rebuild (and stays useful for
        administrative enumeration).
        """
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
        except Exception:
            raise ValueError(f"Pattern '{pattern}' already exists")
        self.invalidate_plan()
        if hasattr(self._db, "last_insert_id"):
            return self._db.last_insert_id()
        return 0  # Fallback

    def remove_pattern(self, pattern_id: int) -> bool:
        """Remove a pattern from the blacklist."""
        self._db.execute("DELETE FROM username_blacklist WHERE id = ?", (pattern_id,))
        self.invalidate_plan()
        return True

    def _get_plan(self) -> Tuple[_PreparedPattern, ...]:
        """Return the current derived plan, rebuilding from the DB when stale.

        The DB read is intentionally outside the fast path but *inside* the
        lock on a rebuild: concurrent first-callers coalesce on one query,
        while callers with a live plan never take the lock at all. A failed
        read propagates (no fallback verdict is fabricated), so the check
        fails closed rather than silently treating a broken DB as "allowed".
        """
        plan = self._plan
        if plan is not None and time.monotonic() - plan.built_at < _PLAN_TTL_SECONDS:
            return plan.patterns

        with self._plan_lock:
            plan = self._plan
            if (
                plan is not None
                and time.monotonic() - plan.built_at < _PLAN_TTL_SECONDS
            ):
                return plan.patterns

            rows = self.get_blacklist()
            prepared: List[_PreparedPattern] = []
            for entry in rows:
                pattern = entry["pattern"]
                is_regex = bool(entry["is_regex"])
                reason = entry["reason"] or "Reserved or prohibited term"
                compiled = None
                normalized = ""
                raw = ""
                if is_regex:
                    try:
                        compiled = _safe_regex.compile(f"(?i){pattern}")
                    except Exception:
                        # An invalid stored regex is skipped exactly as the
                        # previous per-check code skipped it; it can never
                        # turn into an unconditional match.
                        compiled = None
                else:
                    normalized = self._normalize(pattern)
                    raw = pattern.lower().strip()
                prepared.append(
                    _PreparedPattern(is_regex, compiled, normalized, raw, reason)
                )

            patterns = tuple(prepared)
            if len(patterns) <= _MAX_CACHED_PATTERNS:
                self._plan = _Plan(patterns, time.monotonic())
            else:
                # Oversized catalogues stay authoritative on every call.
                self._plan = None
            return patterns

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

        # Patterns come from the short-lived derived plan: one bounded DB read
        # and one normalisation/compile per pattern instead of per check.
        for entry in self._get_plan():
            reason = entry.reason
            if entry.is_regex:
                if entry.compiled is None:
                    continue  # Invalid stored regex: same skip as before.
                try:
                    if entry.compiled.search(username) or entry.compiled.search(
                        normalized
                    ):
                        return True, reason
                except (re.error, TypeError):
                    continue  # Skip an entry that fails at match time.
            else:
                # Exact-match only (case-insensitive), including normalized matching
                if entry.normalized and entry.normalized == normalized:
                    return True, reason
                if entry.raw == username.lower().strip():
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
