"""Tests for the startup presence sweep (sweep_all_offline).

The sweep must be serialized across workers via the distributed lock and
must still clean up ghost rows when no other worker holds the lock
(single-node deployments).
"""

import uuid

import pytest

from src.core.presence import UserStatus
from src.core.presence.manager.status import StatusMixin


@pytest.fixture
def manager(modules):
    return modules.presence


def _make_user(auth_manager, pii_gen, tag):
    from src.utils import encryption

    from unittest.mock import patch

    unique = uuid.uuid4().hex[:8]
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        return auth_manager.register(
            username=f"sweep_{tag}_{unique}",
            email=f"sweep_{tag}_{unique}@example.com",
            password="TestPass123!",
        )


def test_sweep_resets_live_rows_to_offline(manager, auth_manager, pii_gen):
    user = _make_user(auth_manager, pii_gen, "live")
    manager.set_status(user.id, UserStatus.ONLINE)
    assert manager.get_status(user.id) is UserStatus.ONLINE

    swept = manager.sweep_all_offline()

    assert swept >= 1
    assert manager.get_status(user.id) is UserStatus.OFFLINE


def test_sweep_skipped_when_lock_held_elsewhere(manager, monkeypatch):
    """When another worker holds the distributed lock, this worker must NOT
    touch any rows (returns 0) and must not release a lock it doesn't own."""
    import src.core.presence.manager.status as status_mod

    released = []
    acquired = []

    class _FakeClient:
        def acquire_lock(self, key, timeout=5.0, lock_timeout=15000):
            acquired.append(key)
            return None  # lock busy

        def release_lock(self, key, token):
            released.append((key, token))
            return True

    monkeypatch.setattr(status_mod, "valkey_available", lambda: True)
    monkeypatch.setattr(status_mod, "get_valkey_client", lambda: _FakeClient())

    count = StatusMixin.sweep_all_offline(manager)

    assert count == 0
    assert acquired == ["presence:startup_sweep"]
    assert released == []


def test_sweep_runs_and_releases_lock_when_acquired(
    manager, auth_manager, pii_gen, monkeypatch
):
    """Single-node path: lock is free, ghost rows are wiped, lock released."""
    import src.core.presence.manager.status as status_mod

    released = []

    class _FakeClient:
        def acquire_lock(self, key, timeout=5.0, lock_timeout=15000):
            return "token-123"

        def release_lock(self, key, token):
            released.append((key, token))
            return True

    monkeypatch.setattr(status_mod, "valkey_available", lambda: True)
    monkeypatch.setattr(status_mod, "get_valkey_client", lambda: _FakeClient())

    user = _make_user(auth_manager, pii_gen, "dnd")
    manager.set_status(user.id, UserStatus.DND)

    count = StatusMixin.sweep_all_offline(manager)

    assert count >= 1
    assert manager.get_status(user.id) is UserStatus.OFFLINE
    assert released == [("presence:startup_sweep", "token-123")]
