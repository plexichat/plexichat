"""Block/unblock verdicts must not be re-cached by a check during the write.

``IpBlacklistMixin.is_ip_blocked`` memoises each per-IP verdict for 300s under
``ip_blocked:*``. ``block_ip`` / ``unblock_ip`` used to call
``invalidate_pattern("ip_blocked:*")`` at the TOP of the method and only then
perform the authoritative DB write (``upsert`` / ``DELETE``). A check landing
in that window re-reads the still-old row and re-caches the stale verdict, so
the block/unblock completes but the cached answer keeps the gate in its old
state for the rest of the TTL:

* ``block_ip``: a just-blocked attacker IP keeps reporting "not blocked";
* ``unblock_ip``: a just-unblocked IP keeps reporting "blocked".

This is the same before-write invalidation race the mass-session-revocation
fix closed on ``token_verify:*``, on a different security gate. The regression
deterministically triggers a concurrent check at the instant the write runs.
A control IP is warmed alongside the target so a fix that simply flushes too
much (or breaks the cache) is caught too.
"""

import pytest

from src.core.database import invalidate_pattern

pytestmark = pytest.mark.auth

_TARGET = "203.0.113.77"
_CONTROL = "198.51.100.44"


@pytest.fixture(autouse=True)
def _clear_ip_block_cache():
    invalidate_pattern("ip_blocked:*")
    yield
    invalidate_pattern("ip_blocked:*")


def _manager():
    from src.core.auth._lazy import _get_auth_manager

    return _get_auth_manager().get_instance()


def _install_interleaving(monkeypatch, db, mgr, trigger_sql=None):
    """Re-check the block verdict at the instant the authoritative write runs.

    ``trigger_sql`` (when given) limits the injection to a specific DELETE so
    the ``block_ip`` upsert in the unblock test is not itself intercepted. The
    ``in_verify`` guard stops the nested lookup from recursing.
    """
    state = {"in_verify": False, "recached": False}
    original_upsert = db.upsert
    original_execute = db.execute

    def concurrent_check():
        if state["in_verify"] or state["recached"]:
            return
        state["recached"] = True
        state["in_verify"] = True
        try:
            mgr.is_ip_blocked(_TARGET)
        finally:
            state["in_verify"] = False

    def upsert_with_check(*args, **kwargs):
        if trigger_sql is None:
            concurrent_check()
        return original_upsert(*args, **kwargs)

    def execute_with_check(sql, *args, **kwargs):
        if trigger_sql is not None and trigger_sql in str(sql):
            concurrent_check()
        return original_execute(sql, *args, **kwargs)

    monkeypatch.setattr(db, "upsert", upsert_with_check)
    monkeypatch.setattr(db, "execute", execute_with_check)
    return state


def test_block_ip_rejects_not_blocked_verdict_recached_during_write(
    db, auth_manager, monkeypatch
):
    mgr = _manager()

    # Both IPs start unblocked; warm both verdicts before the block.
    assert mgr.is_ip_blocked(_TARGET) is False
    assert mgr.is_ip_blocked(_CONTROL) is False

    state = _install_interleaving(monkeypatch, db, mgr)
    assert mgr.block_ip(_TARGET, reason="regression", blocked_by=None) is True
    assert state["recached"], "the test must actually exercise the race window"

    # The just-blocked IP must report blocked despite the mid-write re-warm.
    assert mgr.is_ip_blocked(_TARGET) is True
    # The unrelated, never-blocked IP is still fine.
    assert mgr.is_ip_blocked(_CONTROL) is False


def test_unblock_ip_rejects_blocked_verdict_recached_during_write(
    db, auth_manager, monkeypatch
):
    mgr = _manager()

    assert mgr.block_ip(_TARGET, reason="regression", blocked_by=None) is True
    # Warm the "blocked" verdict before unblocking.
    assert mgr.is_ip_blocked(_TARGET) is True

    state = _install_interleaving(
        monkeypatch, db, mgr, trigger_sql="DELETE FROM auth_ip_blacklist"
    )
    assert mgr.unblock_ip(_TARGET) is True
    assert state["recached"], "the test must actually exercise the race window"

    # The just-unblocked IP must report unblocked despite the mid-write re-warm.
    assert mgr.is_ip_blocked(_TARGET) is False
