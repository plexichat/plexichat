"""Regression tests for settings-manager security hardening.

Two classes of weaknesses are covered:

* Bulk-write caps (``max_bulk_entries`` / ``max_bulk_payload_bytes``) used to
  be enforced only in the HTTP route, leaving ``SettingsManager.set_many`` --
  the documented single entry point -- wide open to an unbounded atomic write.
* The reserved-namespace fields used to be unvalidated, so a malformed or
  injected config could silently fail open and disable the reserved-key
  protection.
"""

import pytest

from src.core.settings import manager as settings_manager
from src.core.settings.exceptions import SettingsBulkLimitExceeded
from src.core.settings.manager import SettingsManager
from src.core.settings.models import SettingsConfig


class _NoOpLogger:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


@pytest.fixture(autouse=True)
def _no_op_logger(monkeypatch):
    # This test module lives outside src/tests, so the shared logger fixture
    # is not collected for it. Stub the module logger to keep the tests fully
    # self-contained.
    monkeypatch.setattr(settings_manager, "logger", _NoOpLogger())


class _MemoryDB:
    """Minimal DB stub supporting the calls made by ``set_many``."""

    def __init__(self):
        self.rows = {}

    def fetch_one(self, sql, params=()):
        if "COUNT(*)" in sql:
            return {"count": len(self.rows)}
        if "SELECT 1" in sql:
            return {"1": 1} if params[1] in self.rows else None
        if "SELECT id, created_at" in sql or "SELECT value" in sql:
            row = self.rows.get(params[1])
            return dict(row) if row else None
        return None

    def execute(self, sql, params=()):
        if sql.lstrip().upper().startswith("INSERT"):
            sid, uid, key, value, created, updated = params
            self.rows[key] = {
                "id": sid,
                "user_id": uid,
                "key": key,
                "value": value,
                "created_at": created,
                "updated_at": updated,
            }
        return None

    def begin_transaction(self):
        pass

    def commit(self):
        pass

    def rollback(self):
        pass


class TestBulkCapsEnforcedInManager:
    def test_entry_cap_rejected_before_db_access(self):
        manager = SettingsManager(None, SettingsConfig(max_bulk_entries=2))
        items = {f"k{i}": "v" for i in range(3)}
        with pytest.raises(SettingsBulkLimitExceeded, match="entries"):
            manager.set_many(1, items)

    def test_payload_cap_rejected_before_db_access(self):
        manager = SettingsManager(None, SettingsConfig(max_bulk_payload_bytes=10))
        with pytest.raises(SettingsBulkLimitExceeded, match="bytes"):
            manager.set_many(1, {"k": "v" * 20})

    def test_within_caps_succeeds(self):
        manager = SettingsManager(
            _MemoryDB(),
            SettingsConfig(max_bulk_entries=2, max_bulk_payload_bytes=100),
        )
        # ID generation pulls in the global snowflake logger; stub it so the
        # test stays self-contained.
        manager._generate_id = lambda: 123
        results = manager.set_many(1, {"a": "1", "b": "2"})
        assert set(results) == {"a", "b"}
        assert manager.get(1, "a") == "1"

    def test_non_string_value_rejected(self):
        manager = SettingsManager(None)
        # A container's len() counts elements, not bytes: it must not be able
        # to slip past the per-value and bulk byte caps.
        with pytest.raises(ValueError, match="must be a string"):
            manager.set_many(1, {"k": ["x" * 100000]})


class TestReservedNamespaceFailClosed:
    def test_empty_reserved_prefixes_rejected(self):
        manager = SettingsManager(None, SettingsConfig(reserved_prefixes=()))
        with pytest.raises(ValueError, match="reserved_prefixes"):
            manager._validate_key("theme")

    def test_malformed_reserved_keys_rejected(self):
        # A string is iterable and would otherwise be silently treated as a
        # sequence of single-character prefixes, weakening the reservation.
        manager = SettingsManager(None, SettingsConfig(reserved_keys="__internal"))
        with pytest.raises(ValueError, match="reserved_keys"):
            manager._validate_key("theme")

    def test_internal_key_cannot_be_server_managed(self):
        manager = SettingsManager(
            None, SettingsConfig(server_setting_keys=("__internal",))
        )
        with pytest.raises(ValueError, match="server_setting_keys"):
            manager._validate_server_key("_focused_channel")

    def test_default_config_still_valid(self):
        manager = SettingsManager(None)
        manager._validate_key("theme")
        manager._validate_server_key("_focused_channel")
