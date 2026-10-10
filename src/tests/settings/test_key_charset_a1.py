"""
Agent 27 [A1] regression tests: settings key charset / reserved-namespace guards.

Covers the arbitrary-key injection finding in src/core/settings/manager.py:
- Evil keys ("../etc/passwd", "a:b", "key with spaces", "", "x"*101) are
  rejected on BOTH the set and delete paths (delete previously validated
  nothing, letting attacker input flow into cache-invalidation patterns).
- Legit keys (lowercase/uppercase/digits/_/-) still pass (backwards compat;
  dots are NOT part of the documented charset and stay rejected).
- Reserved namespaces (__internal*, __system*, _*) stay rejected on the
  user-facing paths.
- Validation failures are typed ValueErrors (honest 400 at the route layer)
  and carry static messages that never reflect the raw key (the bulk route
  surfaces str(e) in per-key failure bodies).
"""

import pytest

from src.core.settings.exceptions import (
    SettingsError,
    SettingsKeyInvalid,
    SettingsKeyReserved,
    SettingsKeyTooLong,
)
from src.core.settings.manager import SettingsManager


@pytest.fixture
def manager(db):
    return SettingsManager(db)


EVIL_KEYS = [
    "../etc/passwd",
    "..\\windows\\system32",
    "a:b",
    "key with spaces",
    "semi;key",
    "dot.key",
    "star*key",
    "日本語",
    "",
    "   ",
    "x" * 101,
]

LEGIT_KEYS = [
    "theme",
    "Theme",
    "font_size",
    "plexiscribe-dark-mode",
    "a" * 100,
    "UPPER_CASE-123",
]

RESERVED_KEYS = [
    "__internal_secret",
    "__internal",
    "__system_config",
    "__system",
    "_focused_channel",
    "_anything",
]


class TestA1EvilKeysRejected:
    @pytest.mark.parametrize("key", EVIL_KEYS)
    def test_set_rejects_evil_keys(self, manager, test_user, key):
        with pytest.raises(SettingsError):
            manager.set_setting(test_user.id, key, "v")

    @pytest.mark.parametrize("key", EVIL_KEYS)
    def test_delete_rejects_evil_keys(self, manager, test_user, key):
        with pytest.raises(SettingsError):
            manager.delete_setting(test_user.id, key)

    @pytest.mark.parametrize("key", EVIL_KEYS)
    def test_bulk_rejects_evil_keys(self, manager, test_user, key):
        with pytest.raises(SettingsError):
            manager.set_many(test_user.id, {key: "v"})

    def test_overlong_key_raises_too_long(self, manager, test_user):
        with pytest.raises(SettingsKeyTooLong):
            manager.set_setting(test_user.id, "x" * 101, "v")
        with pytest.raises(SettingsKeyTooLong):
            manager.delete_setting(test_user.id, "x" * 101)

    def test_evil_key_never_stored(self, manager, test_user):
        for key in ("../etc/passwd", "a:b"):
            try:
                manager.set_setting(test_user.id, key, "1")
            except SettingsError:
                pass
        assert manager.get_all_settings(test_user.id) == {}


class TestA1LegitKeysPass:
    @pytest.mark.parametrize("key", LEGIT_KEYS)
    def test_set_accepts_legit_keys(self, manager, test_user, key):
        result = manager.set_setting(test_user.id, key, "v")
        assert result.key == key
        assert manager.get_setting(test_user.id, key) == "v"

    def test_delete_accepts_legit_key(self, manager, test_user):
        manager.set_setting(test_user.id, "to_delete_a1", "v")
        assert manager.delete_setting(test_user.id, "to_delete_a1") is True


class TestA1ReservedNamespaces:
    @pytest.mark.parametrize("key", RESERVED_KEYS)
    def test_set_rejects_reserved(self, manager, test_user, key):
        with pytest.raises(SettingsKeyReserved):
            manager.set_setting(test_user.id, key, "v")

    @pytest.mark.parametrize("key", RESERVED_KEYS)
    def test_delete_rejects_reserved(self, manager, test_user, key):
        with pytest.raises(SettingsKeyReserved):
            manager.delete_setting(test_user.id, key)


class TestA1TypedStaticErrors:
    def test_key_errors_are_value_errors(self):
        # The route layer maps typed ValueErrors to an honest 400.
        for exc in (SettingsKeyInvalid, SettingsKeyTooLong, SettingsKeyReserved):
            assert issubclass(exc, ValueError)

    @pytest.mark.parametrize("key", ["../etc/passwd", "__internal_x", "x" * 101])
    def test_messages_do_not_reflect_raw_key(self, manager, test_user, key):
        with pytest.raises(SettingsError) as exc_info:
            manager.set_setting(test_user.id, key, "v")
        assert key not in str(exc_info.value)
        with pytest.raises(SettingsError) as exc_info:
            manager.delete_setting(test_user.id, key)
        assert key not in str(exc_info.value)
