"""Regression: soundboard discovery ``limit`` must be bounded at the core boundary.

The manager methods used ``max(1, limit)`` inline: an oversized value (e.g.
``10**9``) reached SQL ``LIMIT ?`` verbatim and forced the whole usage/history
result set to be materialised, and type-confused values raised or flowed
through unchecked. All three now route through ``_bounded_sound_limit`` which
caps the upper end and rejects non-integer/bool inputs.
"""

from src.core.soundboard._playback import MAX_DISCOVERY_LIMIT, _bounded_sound_limit


class TestBoundedSoundLimitHelper:
    def test_in_range_limits_preserved(self):
        assert _bounded_sound_limit(1) == 1
        assert _bounded_sound_limit(25) == 25
        assert _bounded_sound_limit(MAX_DISCOVERY_LIMIT) == MAX_DISCOVERY_LIMIT

    def test_negative_and_zero_floor_to_one(self):
        assert _bounded_sound_limit(0) == 1
        assert _bounded_sound_limit(-5) == 1

    def test_oversized_limit_is_capped(self):
        assert _bounded_sound_limit(10**9) == MAX_DISCOVERY_LIMIT

    def test_type_confusion_fails_closed(self):
        assert _bounded_sound_limit(True) == 1
        assert _bounded_sound_limit("25") == 25
        assert _bounded_sound_limit("٢٥") == 1
        assert _bounded_sound_limit(2.5) == 1
        assert _bounded_sound_limit(float("nan")) == 1
        assert _bounded_sound_limit(None) == 10


class TestSoundboardListingUpperBoundAtDb:
    def _spy(self, soundboard_manager, monkeypatch):
        captured = {}
        original = soundboard_manager._db.fetch_all

        def spy(statement, params=()):
            if "LIMIT ?" in statement:
                captured["limit"] = params[-1]
            return original(statement, params)

        monkeypatch.setattr(soundboard_manager._db, "fetch_all", spy)
        return captured

    def test_popular_limit_is_capped(self, soundboard_manager, monkeypatch):
        captured = self._spy(soundboard_manager, monkeypatch)
        soundboard_manager.get_popular_sounds(1, limit=10**9)
        assert captured["limit"] == MAX_DISCOVERY_LIMIT

    def test_recent_limit_is_capped(self, soundboard_manager, monkeypatch):
        captured = self._spy(soundboard_manager, monkeypatch)
        soundboard_manager.get_recent_sounds(1, 1, limit=10**9)
        assert captured["limit"] == MAX_DISCOVERY_LIMIT

    def test_search_limit_is_capped(self, soundboard_manager, monkeypatch):
        captured = self._spy(soundboard_manager, monkeypatch)
        soundboard_manager.search_sounds(1, None, "airhorn", limit=10**9)
        assert captured["limit"] == MAX_DISCOVERY_LIMIT
