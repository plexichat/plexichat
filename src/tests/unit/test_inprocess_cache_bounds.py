"""Bounded in-process registries: artifact append locks and invite windows.

Both registries are process-global and keyed by an id that grows with traffic
(artifact ids / user ids). Left unbounded they leak one entry per distinct id
for the life of the process. Each must stay bounded while preserving its
functional guarantee:

* per-artifact append serialization must survive eviction (never drop a lock
  that is currently held);
* the per-user invite-creation rate limit must still reject the N+1th call,
  and reclamation must never shorten a configured window.

Run with: pytest -m unit src/tests/unit/test_inprocess_cache_bounds.py
"""

import threading
import time

import pytest

from src.core.artifacts import repository
from src.core.servers.handlers.members import invites


pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_registries():
    repository._reset_append_locks()
    invites._reset_invite_windows()
    yield
    repository._reset_append_locks()
    invites._reset_invite_windows()


# --- artifact append-lock registry -----------------------------------------


def test_append_lock_registry_is_bounded(monkeypatch):
    monkeypatch.setattr(repository, "_APPEND_LOCKS_MAX", 4)

    for artifact_id in range(200):
        with repository._append_lock(artifact_id):
            pass

    assert len(repository._APPEND_LOCKS) <= 4


def test_append_lock_never_evicts_an_in_use_lock(monkeypatch):
    monkeypatch.setattr(repository, "_APPEND_LOCKS_MAX", 2)

    with repository._append_lock(1) as held:
        entry = repository._APPEND_LOCKS[1]
        assert entry.refs == 1
        # Enough distinct ids to force many eviction cycles.
        for artifact_id in range(2, 50):
            with repository._append_lock(artifact_id):
                pass
        # The held lock was never reclaimed and still guards this artifact.
        assert repository._APPEND_LOCKS.get(1) is entry
        assert entry.refs == 1
        assert held is entry.lock

    # Once the last guard leaves, the entry is idle (eligible for eviction).
    assert repository._APPEND_LOCKS[1].refs == 0


def test_append_lock_serializes_same_artifact():
    entered = []
    release = threading.Event()

    def holder():
        try:
            with repository._append_lock(77):
                entered.append("first")
                release.wait(5)
        finally:
            release.set()

    def waiter():
        with repository._append_lock(77):
            entered.append("second")

    t1 = threading.Thread(target=holder)
    t1.start()
    deadline = time.monotonic() + 5
    while not entered and time.monotonic() < deadline:
        time.sleep(0.01)
    assert entered == ["first"]

    t2 = threading.Thread(target=waiter)
    t2.start()
    time.sleep(0.05)
    assert entered == ["first"]  # second thread is blocked on the same mutex

    release.set()
    t1.join(5)
    t2.join(5)
    assert not t1.is_alive() and not t2.is_alive()
    assert entered == ["first", "second"]


# --- invite-creation window registry ---------------------------------------


def test_invite_window_registry_is_bounded(monkeypatch):
    monkeypatch.setattr(invites, "_INVITE_WINDOW_MAX_KEYS", 4)
    cfg = {"creation_window_seconds": 3600, "creation_max_per_window": 20}

    for uid in range(200):
        invites._check_invite_creation_rate(uid, cfg)

    assert len(invites._invite_creation_windows) <= 4


def test_invite_window_reclaims_idle_buckets():
    cfg = {"creation_window_seconds": 60, "creation_max_per_window": 5}
    invites._check_invite_creation_rate(1, cfg)
    # Age the only bucket past the reclamation floor.
    invites._invite_creation_windows["1"] = [
        time.monotonic() - invites._INVITE_WINDOW_TTL_SECONDS - 10
    ]
    invites._check_invite_creation_rate(2, cfg)

    assert "1" not in invites._invite_creation_windows
    assert "2" in invites._invite_creation_windows


def test_invite_window_still_enforces_rate_limit():
    cfg = {"creation_window_seconds": 3600, "creation_max_per_window": 2}

    invites._check_invite_creation_rate(42, cfg)
    invites._check_invite_creation_rate(42, cfg)
    with pytest.raises(invites.InviteRateLimitedError):
        invites._check_invite_creation_rate(42, cfg)


def test_invite_window_reclamation_does_not_shorten_configured_window():
    # A window longer than the reclamation floor must not be cut short: the
    # retention window is the larger of the two, so the bucket survives.
    cfg = {"creation_window_seconds": 200_000, "creation_max_per_window": 2}
    invites._check_invite_creation_rate(7, cfg)
    invites._check_invite_creation_rate(8, cfg)

    assert "7" in invites._invite_creation_windows
