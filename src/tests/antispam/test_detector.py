"""DM Anti-Spam detector - behavioral coverage of every check branch.

Rate limiting (default + new-user + custom recipient filter), duplicate
content detection with window expiry, pattern-based spam flags (URLs,
mass mentions, invite links), spam-event statistics, and filter CRUD.
The cache tier is stubbed with an in-memory dict so timing is fully
deterministic and no Valkey is required.
"""

from __future__ import annotations

import pytest

import src.core.antispam.detector as detector_mod
from src.core.antispam.detector import DMSpamDetector

pytestmark = pytest.mark.integration

# Fixed reference timestamps (milliseconds). Message timestamps are
# second-granularity after // 1000.
T0_MS = 1_000_000_000_000


@pytest.fixture
def mem_cache(monkeypatch):
    """Route the detector's cache tier through a local dict."""
    cache = {}

    def fake_get(key):
        return cache.get(key)

    def fake_set(key, value, ttl=None):
        cache[key] = value
        return True

    monkeypatch.setattr(detector_mod, "cache_get", fake_get)
    monkeypatch.setattr(detector_mod, "cache_set", fake_set)
    return cache


def _frozen_detector(db, mem_cache, now=None):
    """Build a detector whose clock returns ``now`` (or stays fixed)."""
    detector = DMSpamDetector(db)
    state = {"now": now if now is not None else T0_MS}

    def _ts():
        return state["now"]

    detector._get_timestamp = _ts
    detector._tick = lambda ms: state.update(now=state["now"] + ms)
    return detector


class TestDMSpamDetector:
    def test_benign_message_allowed(self, db):
        detector = DMSpamDetector(db)
        assert detector.check_message(1, 2, "hello friend, how are you?") == (
            True,
            None,
        )

    def test_rate_limit_blocks_after_default_threshold(self, db, mem_cache):
        detector = _frozen_detector(db, mem_cache)
        for i in range(DMSpamDetector.DEFAULT_RATE_LIMIT):
            allowed, reason = detector.check_message(1, 50, f"normal message {i}")
            assert allowed is True, f"message {i} should pass"
            assert reason is None
        allowed, reason = detector.check_message(1, 50, "the one after the limit")
        assert allowed is False
        assert reason is not None
        assert "Rate limit exceeded" in reason
        assert f"{DMSpamDetector.DEFAULT_RATE_LIMIT} messages" in reason

    def test_rate_limit_window_evicts_stale_timestamps(self, db, mem_cache):
        detector = _frozen_detector(db, mem_cache)
        for i in range(DMSpamDetector.DEFAULT_RATE_LIMIT):
            assert detector.check_message(1, 51, f"msg {i}")[0] is True
        # Advance past the rate window: the old timestamps no longer count.
        detector._tick((DMSpamDetector.DEFAULT_RATE_WINDOW + 1) * 1000)
        assert detector.check_message(1, 51, "fresh start") == (True, None)

    def test_rate_limit_is_per_recipient_pair(self, db, mem_cache):
        detector = _frozen_detector(db, mem_cache)
        for i in range(DMSpamDetector.DEFAULT_RATE_LIMIT):
            assert detector.check_message(1, 52, f"to bob {i}")[0] is True
        # A different recipient has a clean counter.
        assert detector.check_message(1, 53, "to carol") == (True, None)

    def test_new_user_gets_stricter_limit(self, db, mem_cache, monkeypatch):
        detector = _frozen_detector(db, mem_cache)
        monkeypatch.setattr(detector, "_is_new_user", lambda sender: True)
        for i in range(DMSpamDetector.DEFAULT_NEW_USER_RATE_LIMIT):
            assert detector.check_message(1, 54, f"new user msg {i}")[0] is True
        allowed, reason = detector.check_message(1, 54, "sixth message")
        assert allowed is False
        assert f"{DMSpamDetector.DEFAULT_NEW_USER_RATE_LIMIT} messages" in reason

    def test_duplicate_content_blocked_after_threshold(self, db, mem_cache):
        detector = _frozen_detector(db, mem_cache)
        content = "buy crypto now"
        # The threshold counts the identical messages already in the window,
        # so the (N+1)-th identical send is the one that trips.
        for _ in range(DMSpamDetector.DEFAULT_DUPLICATE_THRESHOLD):
            assert detector.check_message(1, 60, content)[0] is True
        allowed, reason = detector.check_message(1, 60, content)
        assert allowed is False
        assert reason == "Duplicate message detected"

    def test_duplicate_window_expiry_allows_old_content(self, db, mem_cache):
        detector = _frozen_detector(db, mem_cache)
        content = "repeated link spam"
        for _ in range(DMSpamDetector.DEFAULT_DUPLICATE_THRESHOLD - 1):
            assert detector.check_message(1, 61, content)[0] is True
        # Advance past the duplicate window so the identical content resets.
        detector._tick((DMSpamDetector.DEFAULT_DUPLICATE_WINDOW + 1) * 1000)
        assert detector.check_message(1, 61, content) == (True, None)

    def test_similar_but_distinct_content_not_flagged(self, db, mem_cache):
        detector = _frozen_detector(db, mem_cache)
        for i in range(DMSpamDetector.DEFAULT_DUPLICATE_THRESHOLD):
            assert (
                detector.check_message(1, 62, f"unique message number {i}")[0] is True
            )

    def test_multiple_urls_plus_mentions_flagged(self, db, mem_cache):
        detector = _frozen_detector(db, mem_cache)
        content = (
            "http://spam.example.com/a http://spam.example.com/b "
            "http://spam.example.com/c <@101> <@102> <@103> <@104> <@105>"
        )
        allowed, reason = detector.check_message(1, 70, content)
        assert allowed is False
        assert reason == "Message flagged as potential spam"

    def test_urls_alone_below_threshold_allowed(self, db, mem_cache):
        detector = _frozen_detector(db, mem_cache)
        content = "http://one.example.com/x http://two.example.com/y"
        assert detector.check_message(1, 71, content) == (True, None)

    def test_invite_link_with_urls_flagged(self, db, mem_cache):
        detector = _frozen_detector(db, mem_cache)
        content = (
            "https://a.example.com https://b.example.com https://c.example.com "
            "join discord.gg/xyz"
        )
        allowed, reason = detector.check_message(1, 72, content)
        assert allowed is False
        assert reason == "Message flagged as potential spam"

    def test_invite_link_alone_allowed(self, db, mem_cache):
        detector = _frozen_detector(db, mem_cache)
        assert detector.check_message(1, 73, "join us on discord.gg/friends") == (
            True,
            None,
        )

    def test_custom_recipient_filter_threshold(self, db, mem_cache):
        detector = DMSpamDetector(db)
        detector._get_timestamp = lambda: T0_MS
        detector.set_filter(
            user_id=2, filter_type="rate", threshold=2, window_seconds=60
        )
        assert detector.check_message(1, 2, "first") == (True, None)
        assert detector.check_message(1, 2, "second") == (True, None)
        allowed, reason = detector.check_message(1, 2, "third")
        assert allowed is False
        assert "Rate limit exceeded" in reason
        assert "2 messages per 60s" in reason

    def test_spam_stats_records_blocked_events(self, db, mem_cache):
        detector = _frozen_detector(db, mem_cache)
        for i in range(DMSpamDetector.DEFAULT_RATE_LIMIT):
            detector.check_message(1, 80, f"msg {i}")
        detector.check_message(1, 80, "blocked one")  # triggers rate_limited
        stats = detector.get_spam_stats(80)
        assert stats["user_id"] == 80
        assert stats["period_hours"] == 24
        assert stats["events_by_type"].get("rate_limited", 0) >= 1
        assert any(s["sender_id"] == 1 for s in stats["top_spammers"])

    def test_spam_stats_empty_for_clean_user(self, db, mem_cache):
        detector = DMSpamDetector(db)
        stats = detector.get_spam_stats(999_999)
        assert stats["events_by_type"] == {}
        assert stats["top_spammers"] == []

    def test_set_filter_creates_and_updates(self, db):
        detector = DMSpamDetector(db)
        created = detector.set_filter(user_id=2, threshold=10, window_seconds=60)
        assert created == {"user_id": 2, "filter_type": "rate", "action": "warn"}
        updated = detector.set_filter(
            user_id=2, threshold=3, window_seconds=30, action="block"
        )
        assert updated["action"] == "block"
        rows = detector.get_filters(2)
        assert len(rows) == 1
        assert rows[0]["threshold"] == 3
        assert rows[0]["window_seconds"] == 30
        assert rows[0]["action"] == "block"
        assert rows[0]["enabled"] == 1

    def test_get_filters_empty_by_default(self, db):
        detector = DMSpamDetector(db)
        assert detector.get_filters(12345) == []

    def test_remove_filter(self, db):
        detector = DMSpamDetector(db)
        detector.set_filter(user_id=2, threshold=5)
        rows = detector.get_filters(2)
        assert len(rows) == 1
        assert detector.remove_filter(2, rows[0]["id"]) is True
        assert detector.get_filters(2) == []

    def test_trackers_prune_on_interval(self, db, mem_cache):
        detector = DMSpamDetector(db)
        detector._last_prune_s = 0
        detector._rate_tracker["dm_rate:1:2"] = [1, 2, 3]
        detector._duplicate_tracker["dm_dup:1"] = [(1, "h"), (2, "h")]
        # Force the prune branch: last prune in the distant past.
        detector._prune_trackers()
        # Stale timestamps (1s, 2s, 3s vs 10-minute TTL) must be evicted.
        assert detector._rate_tracker == {}
        assert detector._duplicate_tracker == {}
