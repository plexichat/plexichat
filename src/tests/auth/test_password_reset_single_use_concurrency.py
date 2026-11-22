"""Password-reset redemption: single-use under concurrency + rowcount fail-closed.

The authoritative single-use decision in ``PasswordMixin.reset_password`` is the
guarded ``UPDATE ... WHERE used = 0 AND ... AND expires_at > ?``. Two things must
hold for that to be a real security boundary:

* Under a concurrent replay, exactly one claim succeeds; every loser sees
  ``rowcount == 0`` and is rejected before any credential write.
* If the driver cannot report a rowcount at all (DB-API ``None``/``-1``), the
  claim must be DENIED. Treating an unknown rowcount as "not zero" fails open and
  lets every replay rotate the credential, defeating single-use entirely.

This mirrors the fail-closed convention already used by OAuth code exchange and
poll voting in this codebase.
"""

import threading
import time
import uuid
from unittest.mock import patch

import pytest

from src.core.auth.exceptions import TokenInvalidError
from src.core.auth.tokens import create_email_token
from src.utils import encryption


def _make_reset_token(db, auth_manager, label="rstsingle"):
    unique = uuid.uuid4().hex[:10]
    username = f"{label}_{unique}"
    email = f"{label}_{unique}@test.local"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(username, email, "TestPass123!")

    token_id = int(uuid.uuid4().hex[:15], 16) or 1
    full_token, token_hash = create_email_token(token_id)
    now = int(time.time() * 1000)
    db.execute(
        "INSERT INTO auth_email_tokens "
        "(id, user_id, token_hash, token_type, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (token_id, user.id, token_hash, "reset_password", now, now + 3600 * 1000),
    )
    original_hash = db.fetch_one(
        "SELECT password_hash FROM auth_users WHERE id = ?", (user.id,)
    )["password_hash"]
    return user, full_token, token_id, original_hash


def test_concurrent_redemption_exactly_one_wins(db, auth_manager):
    """N simultaneous replays: one claim succeeds, the rest are rejected.

    Non-vacuous: every thread is aligned at the validity SELECT and observed the
    token as live before any claim runs, so the guarded UPDATE (not the earlier
    SELECT) is what discriminates the single winner. Removing either the
    ``used = 0`` guard or the rowcount check makes more than one thread win.
    """
    user, full_token, token_id, original_hash = _make_reset_token(db, auth_manager)

    manager = auth_manager._get_manager()
    n = 8
    start_barrier = threading.Barrier(n)
    read_barrier = threading.Barrier(n)
    saw_live = []
    saw_lock = threading.Lock()
    results = []
    real_fetch_one = manager._db.fetch_one

    def aligned_fetch_one(query, params=()):
        if "SELECT * FROM auth_email_tokens" in query:
            row = real_fetch_one(query, params)
            with saw_lock:
                saw_live.append(bool(row and not row["used"]))
            try:
                read_barrier.wait(timeout=10)
            except threading.BrokenBarrierError:
                pass
            return row
        return real_fetch_one(query, params)

    def worker():
        try:
            start_barrier.wait(timeout=10)
        except threading.BrokenBarrierError:
            pass
        try:
            outcome = auth_manager.reset_password(full_token, "BrandNewPass123!")
        except TokenInvalidError:
            outcome = False
        except Exception as exc:  # surface unexpected errors instead of hiding them
            outcome = f"ERR:{type(exc).__name__}:{exc}"
        results.append(outcome)

    with patch.object(manager._db, "fetch_one", side_effect=aligned_fetch_one):
        threads = [threading.Thread(target=worker) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

    assert len(results) == n
    assert all(r is True or r is False for r in results), results
    # All threads truly raced on a live token (guards against a vacuous test).
    assert len(saw_live) == n and all(saw_live), saw_live
    assert sum(1 for r in results if r is True) == 1, results

    row = db.fetch_one("SELECT used FROM auth_email_tokens WHERE id = ?", (token_id,))
    assert row["used"] == 1
    current = db.fetch_one(
        "SELECT password_hash FROM auth_users WHERE id = ?", (user.id,)
    )
    assert current["password_hash"] != original_hash


class _UnknownRowcountCursor:
    """Cursor returned by a driver that cannot report affected rows."""

    def __init__(self, real):
        self.rowcount = None
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.mark.parametrize("bad_rowcount", [None, -1])
def test_unknown_rowcount_fails_closed(db, auth_manager, bad_rowcount):
    """An unknown rowcount must deny the claim and never rotate the password."""
    user, full_token, token_id, original_hash = _make_reset_token(
        db, auth_manager, "rstrowcount"
    )

    manager = auth_manager._get_manager()
    real_execute = manager._db.execute

    def _execute(sql, params=()):
        cursor = real_execute(sql, params)
        if "UPDATE auth_email_tokens SET used = 1" in sql and "expires_at >" in sql:
            fake = _UnknownRowcountCursor(cursor)
            fake.rowcount = bad_rowcount
            return fake
        return cursor

    with patch.object(manager._db, "execute", side_effect=_execute):
        with pytest.raises(TokenInvalidError):
            auth_manager.reset_password(full_token, "BrandNewPass123!")

    current = db.fetch_one(
        "SELECT password_hash FROM auth_users WHERE id = ?", (user.id,)
    )
    assert current["password_hash"] == original_hash
