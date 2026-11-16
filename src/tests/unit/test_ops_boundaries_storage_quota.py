"""Regression tests for storage-quota override / config boundaries."""

import pytest

from src.core.security.hardening import HardeningViolation
from src.core.storage_quota.manager import (
    MAX_CONFIG_QUOTA_MB,
    MAX_OVERRIDE_LIMIT_BYTES,
    MAX_OVERRIDE_NOTE_LENGTH,
    StorageQuotaManager,
    _bounded_override_note,
)


class _FakeDB:
    """Minimal DB adapter: overrides don't need real persistence here."""

    def __init__(self):
        self.executed = []

    def execute(self, sql, params=()):
        self.executed.append((sql, params))

    def fetch_one(self, sql, params=()):
        if "SUM" in sql or "COUNT" in sql:
            return {"used": 0, "files": 0, "pending": 0}
        return None

    def fetch_all(self, sql, params=()):
        return []

    def begin_transaction(self):
        pass

    def commit(self):
        pass

    def rollback(self):
        pass


def _manager(config=None):
    return StorageQuotaManager(_FakeDB(), config_override=config)


class TestOverrideNoteBounds:
    def test_plain_note_passes(self):
        assert _bounded_override_note("extended for QA") == "extended for QA"
        assert _bounded_override_note(None) is None

    @pytest.mark.parametrize("note", ["a\x00b", "a\r\nX-Injected: 1", "a\x7fb"])
    def test_control_characters_rejected(self, note):
        with pytest.raises(HardeningViolation):
            _bounded_override_note(note)

    def test_overlong_note_rejected(self):
        with pytest.raises(HardeningViolation):
            _bounded_override_note("x" * (MAX_OVERRIDE_NOTE_LENGTH + 1))


class TestOverrideNumericBounds:
    def test_overflow_limit_rejected(self):
        with pytest.raises(HardeningViolation):
            _manager().set_override(1, limit_bytes=MAX_OVERRIDE_LIMIT_BYTES + 1)

    def test_valid_limit_written(self):
        manager = _manager()
        manager.set_override(1, limit_bytes=1024)
        override_writes = [
            params
            for sql, params in manager._db.executed
            if "user_storage_quotas" in sql
        ]
        assert override_writes
        assert override_writes[-1][1] == 1024

    @pytest.mark.parametrize("bad_id", [-1, 0, 2**70])
    def test_bad_override_ids_rejected(self, bad_id):
        with pytest.raises(HardeningViolation):
            _manager().set_override(bad_id, limit_bytes=1024)

    def test_bad_updated_by_rejected(self):
        with pytest.raises(HardeningViolation):
            _manager().set_override(1, limit_bytes=1024, updated_by=2**70)


class TestConfigIntegerBounds:
    def test_default_quota_overflow_rejected(self):
        manager = _manager({"default_quota_mb": 10**30})
        with pytest.raises(HardeningViolation):
            manager._default_limit_bytes()

    def test_reservation_ttl_bounds(self):
        with pytest.raises(HardeningViolation):
            _manager({"reservation_ttl_seconds": 10**30})._reservation_ttl_seconds()
        assert (
            _manager({"reservation_ttl_seconds": 120})._reservation_ttl_seconds() == 120
        )

    def test_config_magnitude_ceiling_is_finite(self):
        assert MAX_CONFIG_QUOTA_MB > 0
        assert MAX_CONFIG_QUOTA_MB < 10**12
