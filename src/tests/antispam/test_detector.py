"""DM Anti-Spam detector - behavioral coverage of every check branch.

Rate limiting (default + new-user + custom recipient filter), duplicate
content detection scoped per conversation with window expiry, pattern
filters (glob, warn/block actions), heuristic spam flags (URLs,
mass mentions, invite links), spam-event statistics, and filter CRUD.
Counter state uses the process-local fallback tier, reset per test so
timing is fully deterministic and no Valkey is required.
"""

from __future__ import annotations

import pytest

import src.core.antispam.detector as detector_mod
from src.core.antispam.detector import DMSpamDetector

pytestmark = pytest.mark.integration

# Fixed reference timestamps (milliseconds). Message timestamps are
# second-granularity after // 1000.
T0_MS = 1_000_000_000_000


@pytest.fixture(autouse=True)
def clean_counters():
    """Start every test with empty process-local window counters."""
    detector_mod.reset_local_windows()
    yield
    detector_mod.reset_local_windows()


def _frozen_detector(db, now=None):
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

    def test_rate_limit_blocks_after_default_threshold(self, db):
        detector = _frozen_detector(db)
        for i in range(DMSpamDetector.DEFAULT_RATE_LIMIT):
            allowed, reason = detector.check_message(1, 50, f"normal message {i}")
            assert allowed is True, f"message {i} should pass"
            assert reason is None
        allowed, reason = detector.check_message(1, 50, "the one after the limit")
        assert allowed is False
        assert reason is not None
        assert "Rate limit exceeded" in reason
        assert f"{DMSpamDetector.DEFAULT_RATE_LIMIT} messages" in reason

    def test_rate_limit_window_evicts_stale_timestamps(self, db):
        detector = _frozen_detector(db)
        for i in range(DMSpamDetector.DEFAULT_RATE_LIMIT):
            assert detector.check_message(1, 51, f"msg {i}")[0] is True
        # Advance past the rate window: the old timestamps no longer count.
        detector._tick((DMSpamDetector.DEFAULT_RATE_WINDOW + 1) * 1000)
        assert detector.check_message(1, 51, "fresh start") == (True, None)

    def test_rate_limit_is_per_recipient_pair(self, db):
        detector = _frozen_detector(db)
        for i in range(DMSpamDetector.DEFAULT_RATE_LIMIT):
            assert detector.check_message(1, 52, f"to bob {i}")[0] is True
        # A different recipient has a clean counter.
        assert detector.check_message(1, 53, "to carol") == (True, None)

    def test_new_user_gets_stricter_limit(self, db, monkeypatch):
        detector = _frozen_detector(db)
        monkeypatch.setattr(detector, "_is_new_user", lambda sender: True)
        for i in range(DMSpamDetector.DEFAULT_NEW_USER_RATE_LIMIT):
            assert detector.check_message(1, 54, f"new user msg {i}")[0] is True
        allowed, reason = detector.check_message(1, 54, "sixth message")
        assert allowed is False
        assert f"{DMSpamDetector.DEFAULT_NEW_USER_RATE_LIMIT} messages" in reason

    def test_duplicate_content_blocked_after_threshold(self, db):
        detector = _frozen_detector(db)
        content = "buy crypto now"
        # The threshold counts the identical messages already in the window,
        # so the (N+1)-th identical send is the one that trips.
        for _ in range(DMSpamDetector.DEFAULT_DUPLICATE_THRESHOLD):
            assert detector.check_message(1, 60, content)[0] is True
        allowed, reason = detector.check_message(1, 60, content)
        assert allowed is False
        assert reason == "Duplicate message detected"

    def test_duplicate_window_expiry_allows_old_content(self, db):
        detector = _frozen_detector(db)
        content = "repeated link spam"
        for _ in range(DMSpamDetector.DEFAULT_DUPLICATE_THRESHOLD - 1):
            assert detector.check_message(1, 61, content)[0] is True
        # Advance past the duplicate window so the identical content resets.
        detector._tick((DMSpamDetector.DEFAULT_DUPLICATE_WINDOW + 1) * 1000)
        assert detector.check_message(1, 61, content) == (True, None)

    def test_similar_but_distinct_content_not_flagged(self, db):
        detector = _frozen_detector(db)
        for i in range(DMSpamDetector.DEFAULT_DUPLICATE_THRESHOLD):
            assert (
                detector.check_message(1, 62, f"unique message number {i}")[0] is True
            )

    def test_multiple_urls_plus_mentions_flagged(self, db):
        detector = _frozen_detector(db)
        content = (
            "http://spam.example.com/a http://spam.example.com/b "
            "http://spam.example.com/c <@101> <@102> <@103> <@104> <@105>"
        )
        allowed, reason = detector.check_message(1, 70, content)
        assert allowed is False
        assert reason == "Message flagged as potential spam"

    def test_urls_alone_below_threshold_allowed(self, db):
        detector = _frozen_detector(db)
        content = "http://one.example.com/x http://two.example.com/y"
        assert detector.check_message(1, 71, content) == (True, None)

    def test_invite_link_with_urls_flagged(self, db):
        detector = _frozen_detector(db)
        content = (
            "https://a.example.com https://b.example.com https://c.example.com "
            "join discord.gg/xyz"
        )
        allowed, reason = detector.check_message(1, 72, content)
        assert allowed is False
        assert reason == "Message flagged as potential spam"

    def test_invite_link_alone_allowed(self, db):
        detector = _frozen_detector(db)
        assert detector.check_message(1, 73, "join us on discord.gg/friends") == (
            True,
            None,
        )

    def test_invite_only_spam_blocked_without_urls(self, db):
        # L4: three invite markers and nothing else must trip the heuristic —
        # previously the invite signal only counted when paired with 2+ URLs
        # or mentions, so invite-only spam scored 0 and always passed.
        detector = _frozen_detector(db)
        content = "discord.gg/abc123 discord.com/invite/def456 t.me/joinxyz"
        allowed, reason = detector.check_message(1, 74, content)
        assert allowed is False
        assert reason == "Message flagged as potential spam"

    def test_two_invites_alone_below_threshold(self, db):
        # Two invites are not yet spam: URL bucket needs 3+, and the invite
        # signal alone contributes only 1 indicator (block threshold is 3).
        detector = _frozen_detector(db)
        content = "discord.gg/abc123 t.me/joinxyz"
        assert detector.check_message(1, 75, content) == (True, None)

    def test_custom_recipient_filter_threshold(self, db):
        detector = DMSpamDetector(db)
        detector._get_timestamp = lambda: T0_MS
        detector.set_filter(
            user_id=2,
            filter_type="rate",
            action="block",
            threshold=2,
            window_seconds=60,
        )
        assert detector.check_message(1, 2, "first") == (True, None)
        assert detector.check_message(1, 2, "second") == (True, None)
        allowed, reason = detector.check_message(1, 2, "third")
        assert allowed is False
        assert "Rate limit exceeded" in reason
        assert "2 messages per 60s" in reason

    def test_spam_stats_records_blocked_events(self, db):
        detector = _frozen_detector(db)
        for i in range(DMSpamDetector.DEFAULT_RATE_LIMIT):
            detector.check_message(1, 80, f"msg {i}")
        detector.check_message(1, 80, "blocked one")  # triggers rate_limited
        stats = detector.get_spam_stats(80)
        assert stats["user_id"] == 80
        assert stats["period_hours"] == 24
        assert stats["events_by_type"].get("rate_limited", 0) >= 1
        assert any(s["sender_id"] == 1 for s in stats["top_spammers"])

    def test_spam_stats_empty_for_clean_user(self, db):
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

    def test_remove_filter_missing_id_returns_false(self, db):
        detector = DMSpamDetector(db)
        detector.set_filter(user_id=2, threshold=5)
        assert detector.remove_filter(2, 999_999) is False

    def test_blocked_attempts_still_count_toward_rate_window(self, db):
        detector = _frozen_detector(db)
        # Trip the rate limit, then keep hammering: every blocked attempt must
        # still extend pressure inside the window.
        for i in range(DMSpamDetector.DEFAULT_RATE_LIMIT + 3):
            allowed, _ = detector.check_message(1, 90, f"burst {i}")
            if i >= DMSpamDetector.DEFAULT_RATE_LIMIT:
                assert allowed is False
        stats = detector.get_spam_stats(90)
        assert stats["events_by_type"].get("rate_limited", 0) == 3

    def test_warn_action_does_not_block(self, db):
        detector = DMSpamDetector(db)
        detector._get_timestamp = lambda: T0_MS
        detector.set_filter(
            user_id=2,
            filter_type="rate",
            action="warn",
            threshold=1,
            window_seconds=60,
        )
        assert detector.check_message(1, 2, "one") == (True, None)
        allowed, reason = detector.check_message(1, 2, "two")
        assert (allowed, reason) == (True, None)
        stats = detector.get_spam_stats(2)
        assert stats["events_by_type"].get("rate_limited_warned", 0) >= 1

    def test_pattern_filter_blocks_matching_content(self, db):
        detector = DMSpamDetector(db)
        detector._get_timestamp = lambda: T0_MS
        detector.set_filter(
            user_id=2, filter_type="pattern", pattern="*free crypto*", action="block"
        )
        assert detector.check_message(1, 2, "hello!") == (True, None)
        allowed, reason = detector.check_message(1, 2, "get FREE crypto now")
        assert allowed is False
        assert reason is not None
        stats = detector.get_spam_stats(2)
        assert stats["events_by_type"].get("pattern_matched", 0) >= 1

    def test_targeted_pattern_filter_only_applies_to_target(self, db):
        detector = DMSpamDetector(db)
        detector._get_timestamp = lambda: T0_MS
        detector.set_filter(
            user_id=2,
            filter_type="pattern",
            pattern="*banned-word*",
            action="block",
            target_user_id=1,
        )
        # Targeted at sender 1 only...
        allowed, _ = detector.check_message(1, 2, "banned-word here")
        assert allowed is False
        # A different sender is untouched by the targeted filter.
        allowed, _ = detector.check_message(3, 2, "banned-word here")
        assert allowed is True

    def test_duplicate_scope_is_per_conversation(self, db):
        detector = _frozen_detector(db)
        content = "happy birthday!"
        for _ in range(DMSpamDetector.DEFAULT_DUPLICATE_THRESHOLD):
            assert detector.check_message(1, 100, content)[0] is True
        # Same text to a DIFFERENT conversation has a clean duplicate counter.
        assert detector.check_message(1, 101, content) == (True, None)

    def test_heuristics_case_insensitive_urls_and_split_mentions(self, db):
        detector = _frozen_detector(db)
        # Mixed-case schemes now count as real URLs (3 x +? still only +2,
        # matching the documented scoring), so this stays under threshold...
        mixed_case = (
            "HTTP://A.Example.COM/x Http://B.Example.COM/y hTTp://C.Example.COM/z"
        )
        assert detector.check_message(1, 110, mixed_case) == (True, None)
        # ...but combined with a discord.com/invite marker (case-insensitive,
        # previously missed form) the message crosses the block threshold.
        evasive = mixed_case + " join https://DISCORD.COM/INVITE/abc12345"
        allowed, reason = detector.check_message(1, 111, evasive)
        assert allowed is False
        assert reason == "Message flagged as potential spam"
        # Whitespace-split mention tokens ("< @id") count after normalization;
        # combined with mass URLs the message crosses the block threshold.
        split = (
            "< @111> < @112> < @113> < @114> < @115> "
            "http://a.example/x http://b.example/y http://c.example/z"
        )
        allowed, reason = detector.check_message(1, 112, split)
        assert allowed is False

    def test_embedded_decoy_schemes_not_counted_as_urls(self, db):
        detector = _frozen_detector(db)
        # Raw-substring counting would see xhttp:// three times; parsed URL
        # counting sees none.
        decoy = "xhttp://a.example http://real one xhttp://b.example xhttp://c.example"
        assert detector.check_message(1, 113, decoy) == (True, None)
