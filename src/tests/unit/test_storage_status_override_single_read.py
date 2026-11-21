"""Quota status must return the override row in the same single read.

The admin storage endpoints used to read ``user_storage_quotas`` twice per
request: once inside the folded status snapshot (for ``limit_bytes`` /
``unlimited``) and again through a dedicated ``get_override`` SELECT (for the
audit fields ``note`` / ``updated_by`` / ``updated_at``). The snapshot already
joins that row, so it can carry the full override and let callers ask for it
with ``include_override=True``, halving the reads for those endpoints.

These tests drive the real manager against the real test database and count
``fetch_one``/``fetch_all`` calls. The old two-read path and the new one-read
path must return byte-for-byte identical data.
"""

from __future__ import annotations

import pytest

from src.core.storage_quota.manager import MIB, StorageQuotaManager

pytestmark = [pytest.mark.unit]

_USER_ID = 42


class _CountingDB:
    """Delegate to the real DB while counting reads."""

    def __init__(self, db):
        object.__setattr__(self, "db", db)
        object.__setattr__(self, "reads", [])

    def __getattr__(self, name):
        attr = getattr(self.db, name)

        def wrapped(*args, **kwargs):
            if name in ("fetch_all", "fetch_one"):
                object.__setattr__(
                    self,
                    "reads",
                    self.reads + [(name, args[0] if args else "")],
                )
            return attr(*args, **kwargs)

        return wrapped


def _manager(db):
    manager = StorageQuotaManager(db, config_override={"default_quota_mb": 1})
    # Keep tier resolution deterministic and, crucially, off the counted DB.
    manager._user_tier = lambda _user_id: "standard"  # pyright: ignore[reportAttributeAccessIssue]
    manager._tier_limit_bytes = lambda _tier: None  # pyright: ignore[reportAttributeAccessIssue]
    return manager


def test_status_folds_override_into_single_read(db):
    setup = _manager(db)
    setup.set_override(
        _USER_ID,
        limit_bytes=2 * MIB,
        note="support credit",
        updated_by=11,
    )

    # Old path: status snapshot + a second dedicated override SELECT.
    before_db = _CountingDB(db)
    before_manager = _manager(before_db)
    before_status = before_manager.get_status(_USER_ID)
    before_override = before_manager.get_override(_USER_ID)
    old_reads = list(before_db.reads)

    # New path: the override rides along in the one status snapshot read.
    after_db = _CountingDB(db)
    after_manager = _manager(after_db)
    after_status = after_manager.get_status(_USER_ID, include_override=True)
    new_reads = list(after_db.reads)

    # Same payload, including the full audit fields.
    assert after_status["override"] == before_override
    assert {k: v for k, v in after_status.items() if k != "override"} == before_status

    # Before: two reads of the same override row. After: one.
    assert len(old_reads) == 2
    assert len(new_reads) == 1
    assert "user_storage_quotas" in new_reads[0][1]
    assert len(new_reads) < len(old_reads)


def test_status_without_override_is_one_read_and_none(db):
    counting = _CountingDB(db)
    manager = _manager(counting)

    status = manager.get_status(_USER_ID, include_override=True)

    # No override row: LEFT JOIN yields NULLs, override is None, still one read.
    assert status["override"] is None
    assert status["source"] == "default"
    assert len(counting.reads) == 1
