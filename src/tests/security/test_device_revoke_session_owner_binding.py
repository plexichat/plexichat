"""Device revocation must never reach a session owned by another account.

``revoke_device`` is an owner-scoped operation: the caller may only tear down
devices they own.  The device row itself is deleted with ``id = ? AND
user_id = ?``, but the follow-up session revocation used to match on the bare
``device_id``.  A single session row whose ``device_id`` points at another
user's device (a stale row after an account/device id reuse, a partial
migration, or any future writer that mis-binds the two) then becomes part of a
different account's revoke blast radius.  This module pins the owner predicate
on that write, matching the binding ``untrust_device`` already applies to the
equivalent session revocation.
"""

from unittest.mock import patch

from src.core.auth._lazy import _get_auth_manager
from src.utils import encryption


def _register_with_device(auth_manager, username: str, fingerprint: str):
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username=username,
            email=f"{username}@example.com",
            password="TestPass123!",
        )
    with patch.object(encryption, "verify_password", return_value=True):
        auth_manager.login(
            username, "TestPass123!", device_info={"fingerprint": fingerprint}
        )
    return user


def test_revoke_device_leaves_foreign_owner_session_live(db, auth_manager):
    """A session of user B that references user A's device is not in scope."""
    alice = _register_with_device(auth_manager, "revoke_owner_alice", "owner_alice_fp")
    bob = _register_with_device(auth_manager, "revoke_owner_bob", "owner_bob_fp")

    mgr = _get_auth_manager().get_instance()
    alice_device = next(
        d for d in mgr.get_devices(alice.id) if d.fingerprint == "owner_alice_fp"
    )

    # Plant the inconsistent row directly: no public write path creates a
    # session whose user_id and device_id belong to different accounts, but
    # the revoke write must stay safe even if one exists.
    foreign_sid = mgr._generate_id()
    now = mgr._get_timestamp()
    mgr._db.execute(
        "INSERT INTO auth_sessions (id, user_id, token_hash, device_id, "
        "created_at, expires_at, last_activity) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            foreign_sid,
            bob.id,
            "foreign-session-hash",
            alice_device.id,
            now,
            now + 10**9,
            now,
        ),
    )

    assert mgr.revoke_device(alice.id, alice_device.id) is True

    row = mgr._db.fetch_one(
        "SELECT revoked FROM auth_sessions WHERE id = ?", (foreign_sid,)
    )
    assert row is not None
    assert row["revoked"] == 0, (
        "revoking Alice's device also revoked Bob's session: session "
        "revocation is not bound to the device owner"
    )


def test_revoke_device_still_revokes_own_sessions(db, auth_manager):
    """The owner predicate must not weaken the legitimate revoke path."""
    alice = _register_with_device(auth_manager, "revoke_self_alice", "revoke_self_fp")
    mgr = _get_auth_manager().get_instance()
    device = next(
        d for d in mgr.get_devices(alice.id) if d.fingerprint == "revoke_self_fp"
    )

    assert mgr.revoke_device(alice.id, device.id) is True

    rows = mgr._db.fetch_all(
        "SELECT revoked FROM auth_sessions WHERE user_id = ? AND device_id = ?",
        (alice.id, device.id),
    )
    assert rows, "fixture should have created at least one session on the device"
    assert all(r["revoked"] == 1 for r in rows)
