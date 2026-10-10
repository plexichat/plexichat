"""
W6-C regression tests for src/core/settings/ and routes/settings.py.

Covers:
- A1: key charset contract + reserved single-underscore namespace
- A2: atomic bulk writes (set_many rollback, no partial state)
- B3: user-scoped defensive cache invalidation pattern (no cross-user eviction)
- B1/B2: invalidation helpers build keys matching the cached decorator layout
"""

import pytest

from src.core.database.cache.base import mem_cache_set, mem_cache_clear_pattern
from src.core.database.cache.serialization import generate_cache_key
from src.core.settings.manager import SettingsManager, SettingsConfig
from src.core.settings.exceptions import (
    SettingsKeyInvalid,
    SettingsKeyReserved,
    SettingsLimitExceeded,
    SettingsValueTooLong,
)


@pytest.fixture
def manager(db):
    return SettingsManager(db)


class TestA1KeyContract:
    """[A1] Arbitrary-key injection: charset + reserved namespaces."""

    @pytest.mark.parametrize(
        "key",
        ["theme", "Theme", "font_size", "plexiscribe-dark-mode", "a" * 100],
    )
    def test_valid_keys_accepted(self, manager, test_user, key):
        result = manager.set_setting(test_user.id, key, "v")
        assert result.key == key

    @pytest.mark.parametrize(
        "key",
        ["", "   ", "has space", "colon:key", "semi;key", "日本語", "dot.key"],
    )
    def test_invalid_keys_rejected(self, manager, test_user, key):
        with pytest.raises(SettingsKeyInvalid):
            manager.set_setting(test_user.id, key, "v")

    def test_blank_key_rejected_even_if_short(self, manager, test_user):
        with pytest.raises(SettingsKeyInvalid):
            manager.set_setting(test_user.id, "", "v")

    def test_single_underscore_namespace_reserved(self, manager, test_user):
        with pytest.raises(SettingsKeyReserved):
            manager.set_setting(test_user.id, "_focused_channel", "{}")

    def test_double_underscore_still_reserved(self, manager, test_user):
        with pytest.raises(SettingsKeyReserved):
            manager.set_setting(test_user.id, "__internal_secret", "v")

    def test_server_setting_path_accepts_known_server_keys_only(
        self, manager, test_user
    ):
        result = manager.set_server_setting(test_user.id, "_focused_channel", "{}")
        assert result.key == "_focused_channel"
        # Readable back through the generic getter.
        assert manager.get_setting(test_user.id, "_focused_channel") == "{}"

        with pytest.raises(SettingsKeyReserved):
            manager.set_server_setting(test_user.id, "_not_a_server_key", "v")

    def test_legacy_junk_keys_filtered_on_read(self, db, test_user):
        manager = SettingsManager(db)
        manager.set_setting(test_user.id, "good_key", "v1")
        now = manager._get_timestamp()
        # Simulate a legacy row predating the charset contract.
        db.execute(
            "INSERT INTO user_settings (id, user_id, key, value, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                manager._generate_id(),
                test_user.id,
                "junk:key with spaces",
                "stale",
                now,
                now,
            ),
        )

        all_settings = manager.get_all_settings(test_user.id)
        assert all_settings == {"good_key": "v1"}

        listed = manager.get_settings_list(test_user.id)
        assert [s.key for s in listed] == ["good_key"]

        assert manager.get_setting(test_user.id, "junk:key with spaces") is None


class TestA2AtomicBulk:
    """[A2] Bulk writes are capped and commit atomically."""

    def test_set_many_writes_all(self, db, test_user):
        manager = SettingsManager(db)
        results = manager.set_many(test_user.id, {"k1": "v1", "k2": "v2", "k3": "v3"})
        assert set(results) == {"k1", "k2", "k3"}
        assert manager.get_setting(test_user.id, "k2") == "v2"

    def test_set_many_rolls_back_on_failure(self, db, test_user):
        manager = SettingsManager(db)
        manager.set_setting(test_user.id, "existing", "old")
        bad_batch = {
            "existing": "new",
            "fresh_key": "v",
            "bad key!": "v",  # invalid under the charset contract
        }

        with pytest.raises(SettingsKeyInvalid):
            manager.set_many(test_user.id, bad_batch)

        # Nothing committed: neither the update nor the insert.
        assert manager.get_setting(test_user.id, "existing") == "old"
        assert manager.get_setting(test_user.id, "fresh_key") is None

    def test_set_many_enforces_limit_atomically(self, db, test_user):
        manager = SettingsManager(db, config=SettingsConfig(max_settings_per_user=3))
        with pytest.raises(SettingsLimitExceeded):
            manager.set_many(test_user.id, {"a": "1", "b": "2", "c": "3", "d": "4"})
        assert manager.get_settings_count(test_user.id) == 0

    def test_set_many_rejects_overlong_value(self, db, test_user):
        manager = SettingsManager(db)
        with pytest.raises(SettingsValueTooLong):
            manager.set_many(test_user.id, {"k": "v" * 10001})
        assert manager.get_setting(test_user.id, "k") is None


class TestB3ScopedInvalidation:
    """[B3] Defensive pattern invalidation must be exact-user scoped."""

    def test_fallback_pattern_is_user_scoped(self):
        victim = type("T", (), {"user_id": 111})()
        attacker = type("T", (), {"user_id": 999})()

        victim_key = generate_cache_key(
            "user_setting_api", key="theme", current_user=victim
        )
        attacker_junk_key = generate_cache_key(
            "user_setting_api",
            key=f"x:TokenInfo:{victim.user_id}",
            current_user=attacker,
        )
        mem_cache_set(victim_key, {"v": 1}, 60)
        mem_cache_set(attacker_junk_key, {"v": 1}, 60)

        from src.api.routes.settings import _invalidate_user_settings_cache

        _invalidate_user_settings_cache(victim)

        # The attacker's junk-key entry (other user) must survive.
        assert mem_cache_clear_pattern(attacker_junk_key) == 1

    def test_invalidation_matches_decorator_layout(self):
        token = type("T", (), {"user_id": 4242})()
        per_key = generate_cache_key(
            "user_setting_api", key="theme", current_user=token
        )
        all_key = generate_cache_key("user_settings_all_api", current_user=token)
        mem_cache_set(per_key, {"v": 1}, 60)
        mem_cache_set(all_key, {"v": 1}, 60)

        from src.api.routes.settings import _invalidate_user_settings_cache

        _invalidate_user_settings_cache(token, keys=["theme"])

        # Both targeted entries deleted; re-clearing finds nothing.
        assert mem_cache_clear_pattern(per_key) == 0
        assert mem_cache_clear_pattern(all_key) == 0

    def test_messaging_invalidation_layout(self):
        token = type("T", (), {"user_id": 7})()
        key = generate_cache_key("messaging_settings_api", current_user=token)
        mem_cache_set(key, {"v": 1}, 60)

        from src.core.database.cache.operations import invalidate_cached

        invalidate_cached(key)
        assert mem_cache_clear_pattern(key) == 0
