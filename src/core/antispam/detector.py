"""
DM Spam Detector - Anti-spam heuristics for direct messages.

Provides rate limiting, duplicate message detection, and pattern-based
spam filtering for DM conversations to protect users from harassment and spam.
"""

import time
import hashlib
from typing import Optional, Dict, Any, List, Tuple
from collections import defaultdict

import utils.logger as logger
from src.utils.encryption import generate_snowflake_id
from src.core.database import cache_get, cache_set


class DMSpamDetector:
    """Detects and prevents spam in DM conversations."""

    # Default thresholds
    DEFAULT_RATE_LIMIT = 10  # messages per window
    DEFAULT_RATE_WINDOW = 60  # seconds
    DEFAULT_DUPLICATE_THRESHOLD = 3  # identical messages
    DEFAULT_DUPLICATE_WINDOW = 300  # seconds (5 minutes)
    DEFAULT_NEW_USER_RATE_LIMIT = 5  # stricter for new accounts
    DEFAULT_NEW_USER_AGE_MS = 7 * 24 * 3600 * 1000  # 7 days

    # In-memory tracker eviction settings
    TRACKER_MAX_ENTRIES = 10000  # Max keys in each tracker
    TRACKER_MAX_PER_KEY = 50  # Max items per key
    TRACKER_PRUNE_INTERVAL_S = 300  # Prune every 5 minutes
    TRACKER_TTL_S = 600  # Entries older than 10 minutes are stale

    def __init__(self, db, auth_module=None):
        self._db = db
        self._auth = auth_module
        # In-memory tracking for fast checks (backed by cache)
        self._rate_tracker: Dict[str, List[int]] = defaultdict(list)
        self._duplicate_tracker: Dict[str, List[Tuple[int, str]]] = defaultdict(list)
        self._last_prune_s: int = 0

    def _get_timestamp(self) -> int:
        return int(time.time() * 1000)

    def _generate_id(self) -> int:
        return generate_snowflake_id()

    def _rate_key(self, sender_id: int, recipient_id: int) -> str:
        return f"dm_rate:{sender_id}:{recipient_id}"

    def _duplicate_key(self, sender_id: int) -> str:
        return f"dm_dup:{sender_id}"

    def _is_new_user(self, sender_id: int) -> bool:
        """Check if the sender is a newly registered user."""
        row = self._db.fetch_one(
            "SELECT created_at FROM auth_users WHERE id = ?", (sender_id,)
        )
        if not row:
            return False
        age = self._get_timestamp() - row["created_at"]
        return age < self.DEFAULT_NEW_USER_AGE_MS

    def _get_user_filter(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get DM spam filter settings for a user."""
        row = self._db.fetch_one(
            "SELECT * FROM dm_spam_filters WHERE user_id = ? AND enabled = 1",
            (user_id,),
        )
        return dict(row) if row else None

    def _record_event(
        self,
        sender_id: int,
        recipient_id: int,
        event_type: str,
        content_hash: Optional[str] = None,
    ) -> None:
        """Record a DM spam event for tracking."""
        now = self._get_timestamp()
        event_id = self._generate_id()
        self._db.execute(
            """INSERT INTO dm_spam_events
               (id, sender_id, recipient_id, event_type, content_hash, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (event_id, sender_id, recipient_id, event_type, content_hash, now),
        )

    def _prune_trackers(self) -> None:
        """Evict stale entries and enforce size limits on in-memory trackers."""
        now_s = int(time.time())

        # Only prune periodically
        if now_s - self._last_prune_s < self.TRACKER_PRUNE_INTERVAL_S:
            return
        self._last_prune_s = now_s

        cutoff_s = now_s - self.TRACKER_TTL_S

        # Prune rate tracker: remove old timestamps and empty keys
        stale_keys = []
        for key, timestamps in self._rate_tracker.items():
            filtered = [t for t in timestamps if t > cutoff_s]
            if filtered:
                self._rate_tracker[key] = filtered[-self.TRACKER_MAX_PER_KEY :]
            else:
                stale_keys.append(key)
        for key in stale_keys:
            del self._rate_tracker[key]

        # Prune duplicate tracker: remove old entries and empty keys
        stale_keys = []
        for key, entries in self._duplicate_tracker.items():
            filtered = [(ts, h) for ts, h in entries if ts > cutoff_s]
            if filtered:
                self._duplicate_tracker[key] = filtered[-self.TRACKER_MAX_PER_KEY :]
            else:
                stale_keys.append(key)
        for key in stale_keys:
            del self._duplicate_tracker[key]

        # Hard limit: if still too many keys, evict oldest half
        if len(self._rate_tracker) > self.TRACKER_MAX_ENTRIES:
            keys_to_keep = list(self._rate_tracker.keys())[
                : self.TRACKER_MAX_ENTRIES // 2
            ]
            self._rate_tracker = defaultdict(
                list, {k: self._rate_tracker[k] for k in keys_to_keep}
            )
        if len(self._duplicate_tracker) > self.TRACKER_MAX_ENTRIES:
            keys_to_keep = list(self._duplicate_tracker.keys())[
                : self.TRACKER_MAX_ENTRIES // 2
            ]
            self._duplicate_tracker = defaultdict(
                list, {k: self._duplicate_tracker[k] for k in keys_to_keep}
            )

    def check_message(
        self,
        sender_id: int,
        recipient_id: int,
        content: str,
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if a DM message should be allowed or flagged as spam.

        Args:
            sender_id: ID of the message sender
            recipient_id: ID of the message recipient
            content: Message content

        Returns:
            Tuple of (allowed: bool, reason: Optional[str])
            If allowed is False, reason contains the spam detection reason.
        """
        # Prune stale tracker entries periodically
        self._prune_trackers()

        now_ms = self._get_timestamp()
        now_s = now_ms // 1000

        # 1. Rate limit check
        rate_key = self._rate_key(sender_id, recipient_id)

        # Check recipient's custom filter first
        recipient_filter = self._get_user_filter(recipient_id)
        rate_limit = self.DEFAULT_RATE_LIMIT
        rate_window = self.DEFAULT_RATE_WINDOW

        if recipient_filter:
            rate_limit = recipient_filter.get("threshold", self.DEFAULT_RATE_LIMIT)
            rate_window = recipient_filter.get(
                "window_seconds", self.DEFAULT_RATE_WINDOW
            )

        # New users get stricter limits
        if self._is_new_user(sender_id):
            rate_limit = min(rate_limit, self.DEFAULT_NEW_USER_RATE_LIMIT)

        # Check rate from cache
        cached = cache_get(rate_key)
        if cached:
            timestamps = [t for t in cached if t > now_s - rate_window]
            if len(timestamps) >= rate_limit:
                self._record_event(sender_id, recipient_id, "rate_limited")
                return (
                    False,
                    f"Rate limit exceeded: {rate_limit} messages per {rate_window}s",
                )
        else:
            timestamps = []

        # 2. Duplicate content check
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        dup_key = self._duplicate_key(sender_id)

        cached_dups = cache_get(dup_key)
        if cached_dups:
            # Filter to window
            recent_dups = [
                (ts, h)
                for ts, h in cached_dups
                if ts > now_s - self.DEFAULT_DUPLICATE_WINDOW
            ]
            # Count identical content
            identical = sum(1 for _, h in recent_dups if h == content_hash)
            if identical >= self.DEFAULT_DUPLICATE_THRESHOLD:
                self._record_event(
                    sender_id, recipient_id, "duplicate_spam", content_hash
                )
                return False, "Duplicate message detected"
        else:
            recent_dups = []

        # 3. Pattern-based spam check (URLs, mentions, invites)
        spam_indicators = 0

        # Multiple URLs in a single message
        url_count = content.count("http://") + content.count("https://")
        if url_count >= 3:
            spam_indicators += 2

        # Mass mentions
        mention_count = content.count("<@")
        if mention_count >= 5:
            spam_indicators += 2

        # Invite links
        if "discord.gg/" in content.lower() or "t.me/" in content.lower():
            # Only flag if combined with other indicators
            if url_count >= 2 or mention_count >= 2:
                spam_indicators += 1

        if spam_indicators >= 3:
            self._record_event(sender_id, recipient_id, "pattern_spam", content_hash)
            return False, "Message flagged as potential spam"

        # Message passes all checks - update tracking
        timestamps.append(now_s)
        cache_set(rate_key, timestamps[-50:], ttl=rate_window)

        recent_dups.append((now_s, content_hash))
        cache_set(dup_key, recent_dups[-100:], ttl=self.DEFAULT_DUPLICATE_WINDOW)

        return True, None

    def get_spam_stats(self, user_id: int) -> Dict[str, Any]:
        """Get spam statistics for a user (as recipient)."""
        # Count events in last 24 hours
        day_ago = self._get_timestamp() - (24 * 3600 * 1000)
        rows = self._db.fetch_all(
            "SELECT event_type, COUNT(*) as count FROM dm_spam_events WHERE recipient_id = ? AND created_at > ? GROUP BY event_type",
            (user_id, day_ago),
        )
        stats = {row["event_type"]: row["count"] for row in rows}

        # Get top spammers
        spammer_rows = self._db.fetch_all(
            "SELECT sender_id, COUNT(*) as count FROM dm_spam_events WHERE recipient_id = ? AND created_at > ? GROUP BY sender_id ORDER BY count DESC LIMIT 10",
            (user_id, day_ago),
        )
        top_spammers = [
            {"sender_id": row["sender_id"], "count": row["count"]}
            for row in spammer_rows
        ]

        return {
            "user_id": user_id,
            "period_hours": 24,
            "events_by_type": stats,
            "top_spammers": top_spammers,
        }

    def set_filter(
        self,
        user_id: int,
        filter_type: str = "rate",
        action: str = "warn",
        threshold: int = 10,
        window_seconds: int = 60,
        target_user_id: Optional[int] = None,
        pattern: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Set or update DM spam filter for a user."""
        now = self._get_timestamp()

        existing = self._db.fetch_one(
            "SELECT id FROM dm_spam_filters WHERE user_id = ? AND filter_type = ? AND (target_user_id = ? OR target_user_id IS NULL)",
            (user_id, filter_type, target_user_id),
        )

        if existing:
            self._db.execute(
                """UPDATE dm_spam_filters SET action = ?, threshold = ?,
                   window_seconds = ?, pattern = ?, enabled = 1, updated_at = ?
                   WHERE id = ?""",
                (action, threshold, window_seconds, pattern, now, existing["id"]),
            )
        else:
            filter_id = self._generate_id()
            self._db.execute(
                """INSERT INTO dm_spam_filters
                   (id, user_id, target_user_id, pattern, filter_type, action,
                    threshold, window_seconds, enabled, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (
                    filter_id,
                    user_id,
                    target_user_id,
                    pattern,
                    filter_type,
                    action,
                    threshold,
                    window_seconds,
                    now,
                    now,
                ),
            )

        logger.debug(f"User {user_id} set DM spam filter: type={filter_type}")
        return {"user_id": user_id, "filter_type": filter_type, "action": action}

    def remove_filter(self, user_id: int, filter_id: int) -> bool:
        """Remove a DM spam filter."""
        self._db.execute(
            "DELETE FROM dm_spam_filters WHERE id = ? AND user_id = ?",
            (filter_id, user_id),
        )
        return True

    def get_filters(self, user_id: int) -> List[Dict[str, Any]]:
        """Get all DM spam filters for a user."""
        rows = self._db.fetch_all(
            "SELECT * FROM dm_spam_filters WHERE user_id = ? ORDER BY created_at",
            (user_id,),
        )
        return [dict(row) for row in rows]
