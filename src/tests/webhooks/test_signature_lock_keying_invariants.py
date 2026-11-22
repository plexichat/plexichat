"""Regression: per-webhook signing-lock registry keeps its keying invariant.

N645 made the webhook signing guard per-webhook and backed it with an LRU
registry (bounded at 1024) whose entries are refcounted so an in-use lock is
never evicted. These tests pin the security-relevant keying guarantees:

* the same webhook always resolves to the same mutex, even under LRU pressure;
* distinct webhook ids never share a mutex;
* a malformed/None id fails closed without running the guarded critical
  section with no lock (or a mismatched one);
* ``rotate_signing_key`` and ``verify_payload`` lock with the same key.

If any of these regress, two deliveries for one webhook can enter the
fetch/check/verify/consume section concurrently and a captured signature can be
accepted twice, or a rotation can swap a key mid-verification.
"""

import base64
import threading
import time

import pytest

import src.core.webhooks.manager.signature as sig
from src.core import webhooks as webhooks_module


pytestmark = pytest.mark.unit


def _reset_registry():
    # Tests share the process-global registry; start each from a clean slate.
    with sig._SIGNATURE_LOCKS_GUARD:
        sig._SIGNATURE_LOCKS.clear()


def test_held_lock_survives_lru_pressure_and_seriates_contender():
    """A guard held for X is never evicted; a 2nd caller for X must block.

    With >1024 webhooks, pruning runs on every acquisition. If the refcount
    guard were wrong and X were evicted while held, the contender would get a
    fresh lock and would NOT block -- two threads in X's critical section.
    """
    _reset_registry()
    entered = threading.Event()
    release = threading.Event()

    def holder():
        with sig._signature_lock_for(777) as held:
            assert held is not None
            entered.set()
            release.wait(timeout=10)

    t = threading.Thread(target=holder)
    t.start()
    assert entered.wait(3), "holder never acquired the guard"

    # Force pruning well past the 1024-entry bound with OTHER webhooks.
    for i in range(1, 1030):
        with sig._signature_lock_for(100_000 + i):
            pass

    with sig._SIGNATURE_LOCKS_GUARD:
        assert 777 in sig._SIGNATURE_LOCKS, "held lock was evicted from the registry"

    contender_acquired = threading.Event()

    def contender():
        with sig._signature_lock_for(777):
            contender_acquired.set()

    c = threading.Thread(target=contender)
    c.start()
    time.sleep(0.3)
    blocked = not contender_acquired.is_set()

    release.set()
    t.join(timeout=10)
    c.join(timeout=10)
    assert contender_acquired.is_set(), "contender never acquired the guard"
    assert blocked, "contender did not block: held lock was evicted / duplicated"


def test_distinct_webhook_ids_get_distinct_locks_and_same_id_same_lock():
    """Keying by int(webhook_id) is exact for real (integer) snowflakes."""
    _reset_registry()
    with sig._signature_lock_for(777) as a:
        with sig._signature_lock_for(777) as b:
            assert a is b, "one webhook must use one reentrant mutex"
    with sig._signature_lock_for(777) as a:
        with sig._signature_lock_for(778) as b:
            assert a is not b, "distinct webhooks must not share a mutex"


def test_registry_stays_bounded():
    """Idle locks are reclaimed so the bounded registry cannot grow forever."""
    _reset_registry()
    for i in range(3 * sig._SIGNATURE_LOCKS_MAX):
        with sig._signature_lock_for(i):
            pass
    with sig._SIGNATURE_LOCKS_GUARD:
        assert len(sig._SIGNATURE_LOCKS) <= sig._SIGNATURE_LOCKS_MAX


def test_malformed_id_fails_closed_without_entering_guarded_section(monkeypatch):
    """A None/malformed id cannot run the critical section under no lock.

    ``int(webhook_id)`` raises before any lock is taken; the module-level
    wrapper turns that into a fail-closed ``False``. The DB must never be
    touched for such an id.
    """
    _reset_registry()
    db_calls = []
    lock_calls = []

    real_lock_for = sig._signature_lock_for

    def spy_lock_for(webhook_id):
        lock_calls.append(webhook_id)
        return real_lock_for(webhook_id)

    # Route both the lock acquisition and the DB through instrumented fakes so
    # we can prove the malformed id reached (and failed at) the keying step.
    monkeypatch.setattr(sig, "_signature_lock_for", spy_lock_for)

    class DummyDB:
        def fetch_one(self, *a, **k):
            db_calls.append(a)
            return None

    class DummyManager:
        _db = DummyDB()

        def verify_payload(self, *a, **k):
            return sig.SignatureMixin.verify_payload(self, *a, **k)

    monkeypatch.setattr(webhooks_module, "_get_manager", lambda: DummyManager())

    # A well-formed 64-byte Ed25519 signature header and a FRESH timestamp so
    # execution passes the header/length/skew checks and reaches the keying
    # step at ``with _signature_lock_for(webhook_id)``. Without this, a bad
    # timestamp would return early and make the test vacuous.
    sig_header = base64.b64encode(b"\x00" * 64).decode("ascii")
    now_ms = int(time.time() * 1000)
    ts_header = str(now_ms)

    for bad_id in (None, "not-an-id", object()):
        lock_calls.clear()
        db_calls.clear()
        with sig._SIGNATURE_LOCKS_GUARD:
            before = len(sig._SIGNATURE_LOCKS)
        # The malformed id makes int(webhook_id) raise at the lock keying step;
        # the module wrapper turns that into a fail-closed False.
        assert (
            webhooks_module.verify_payload(
                bad_id, {"a": 1}, sig_header, ts_header, now_ms
            )
            is False
        )
        assert lock_calls == [bad_id], "keying step was not reached for malformed id"
        assert not db_calls, "guarded DB access ran despite int(id) failing"
        with sig._SIGNATURE_LOCKS_GUARD:
            assert len(sig._SIGNATURE_LOCKS) == before, "malformed id created a lock"


def test_rotate_and_verify_use_the_same_lock(webhook_with_token, monkeypatch):
    """Both code paths must resolve to the identical mutex for one webhook."""
    setup = webhook_with_token
    manager = setup["webhooks"]
    wid = setup["webhook"].id
    payload = {"event": "lock-keying"}
    headers = manager.sign_payload(wid, payload)

    real = sig._signature_lock_for
    seen = []

    def spy(webhook_id):
        inner = real(webhook_id)
        captured = {}

        class Wrap:
            def __enter__(self):
                captured["lock"] = inner.__enter__()
                seen.append(captured["lock"])
                return captured["lock"]

            def __exit__(self, *exc):
                return inner.__exit__(*exc)

        return Wrap()

    monkeypatch.setattr(sig, "_signature_lock_for", spy)
    assert manager.verify_payload(
        wid, payload, headers["X-Plexichat-Signature"], headers["X-Plexichat-Timestamp"]
    )
    manager.rotate_signing_key(wid, setup["owner"].id)

    assert len(seen) == 2, f"expected two guarded sections, got {len(seen)}"
    assert seen[0] is seen[1], "rotate and verify locked with different mutexes"
