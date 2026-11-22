"""2FA backup-code redemption: single-use under concurrency, fail-closed,
hashed-at-rest, and uniform errors.

``TwoFactorMixin.complete_2fa`` burns a backup code with a conditional
``UPDATE auth_users SET backup_codes_hash = ? WHERE id = ? AND backup_codes_hash = ?``
and decides the winner from ``cursor.rowcount``.  Two racers that both read the
same stored digest list before either writes must yield exactly one session;
the loser must fail closed with the *same* ``TwoFactorInvalidError("Invalid
code")`` an outright bad code gets, so a burned/concurrently-consumed code is
not distinguishable from a never-valid one (no which-code oracle).

This test drives the real manager against the real connection-pooled SQLite
test DB.  A barrier forces both workers past the ``SELECT * FROM auth_users``
read with the identical stored digest list before either CAS, which is the
check-then-act window a plain (unconditional) UPDATE would lose.
"""

import json
import threading
from unittest.mock import patch

import pytest

from src.core.auth.exceptions import TwoFactorInvalidError
from src.utils import encryption

pytestmark = [pytest.mark.security, pytest.mark.unit]


def _seed_user_with_known_backup_codes(auth_manager, username, codes):
    import pyotp

    from src.core.auth import totp as totp_module
    from src.tests._conftest.totp_clock import frozen_totp_time

    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username, f"{username}@example.com", "TestPass123!"
        )
    setup = auth_manager.setup_2fa(user.id)
    with frozen_totp_time():
        assert auth_manager.confirm_2fa(user.id, pyotp.TOTP(setup.secret).now()) is True
    mgr = auth_manager._get_manager()
    mgr._db.execute(
        "UPDATE auth_users SET backup_codes_hash = ? WHERE id = ?",
        (json.dumps(totp_module.hash_backup_codes(codes)), user.id),
    )
    return user, mgr


def test_backup_codes_are_stored_hashed_not_plaintext(auth_manager):
    codes = ["A1B2-C3D4", "E5F6-G7H8"]
    user, mgr = _seed_user_with_known_backup_codes(auth_manager, "hash_at_rest", codes)

    row = mgr._db.fetch_one(
        "SELECT backup_codes_hash FROM auth_users WHERE id = ?", (user.id,)
    )
    stored = row["backup_codes_hash"]
    for code in codes:
        assert code not in stored, "plaintext backup code leaked to storage"
        assert code.replace("-", "") not in stored
    digests = json.loads(stored)
    assert len(digests) == len(codes)
    for digest in digests:
        assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)


def test_concurrent_redeem_single_use_with_uniform_error(auth_manager, monkeypatch):
    from src.core.auth import totp as totp_module

    code = "A1B2-C3D4"
    other = "E5F6-G7H8"
    user, mgr = _seed_user_with_known_backup_codes(
        auth_manager, "concurrent_redeem", [code, other]
    )

    ch1 = mgr._create_2fa_challenge(user.id, None, None, None)
    ch2 = mgr._create_2fa_challenge(user.id, None, None, None)

    read_barrier = threading.Barrier(2)
    gate = {"on": True}
    real_fetch_one = mgr._db.fetch_one
    real_execute = mgr._db.execute
    cas_rowcounts = []
    cas_lock = threading.Lock()

    def gated_fetch_one(sql, params=()):
        result = real_fetch_one(sql, params)
        if gate["on"] and "FROM auth_users" in sql and "SELECT *" in sql and result:
            # Both workers observe the identical pre-consume digest list before
            # either is allowed past the read into the CAS.
            read_barrier.wait(timeout=10)
        return result

    def observing_execute(sql, params=()):
        cursor = real_execute(sql, params)
        if "UPDATE auth_users" in sql and "backup_codes_hash = ?" in sql:
            with cas_lock:
                cas_rowcounts.append(getattr(cursor, "rowcount", 0))
        return cursor

    monkeypatch.setattr(mgr._db, "fetch_one", gated_fetch_one)
    monkeypatch.setattr(mgr._db, "execute", observing_execute)

    outcomes = []
    outcome_lock = threading.Lock()

    def attempt(token, presented):
        try:
            result = auth_manager.complete_2fa(token, presented)
            outcome = "success" if result.status.value == "success" else "fail"
            error = None
        except TwoFactorInvalidError as exc:
            outcome, error = "invalid", str(exc)
        with outcome_lock:
            outcomes.append((outcome, error))

    threads = [
        threading.Thread(target=attempt, args=(ch1.token, code)),
        threading.Thread(target=attempt, args=(ch2.token, code)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert not any(thread.is_alive() for thread in threads), (
        "worker deadlocked at barrier"
    )
    gate["on"] = False  # subsequent single-threaded checks must not hit the barrier
    assert len(outcomes) == 2

    successes = [o for o in outcomes if o[0] == "success"]
    failures = [o for o in outcomes if o[0] != "success"]
    assert len(successes) == 1, f"exactly one racer may burn the code: {outcomes}"
    assert len(failures) == 1, outcomes

    # The guarded CAS decided the winner via rowcount: one match, one miss.
    assert sorted(cas_rowcounts) == [0, 1], (
        f"expected one CAS miss + one hit: {cas_rowcounts}"
    )

    # Fail-closed AND uniform: the loser's error is byte-identical to the error
    # a never-valid code gets -- no "already used" / which-code oracle.
    loser_error = failures[0][1]
    assert loser_error is not None and "used" not in loser_error.lower(), loser_error

    bad_challenge = mgr._create_2fa_challenge(user.id, None, None, None)
    with pytest.raises(TwoFactorInvalidError) as bad:
        auth_manager.complete_2fa(bad_challenge.token, "DEAD-BEEF")
    assert str(bad.value) == loser_error

    # Exactly one code was consumed: the winner's code is gone, the unrelated
    # code is still present and redeemable.
    row = mgr._db.fetch_one(
        "SELECT backup_codes_hash FROM auth_users WHERE id = ?", (user.id,)
    )
    remaining = json.loads(row["backup_codes_hash"])
    assert len(remaining) == 1
    assert remaining[0] == totp_module._hash_backup_code(other)

    fresh = mgr._create_2fa_challenge(user.id, None, None, None)
    assert auth_manager.complete_2fa(fresh.token, other).status.value == "success"
