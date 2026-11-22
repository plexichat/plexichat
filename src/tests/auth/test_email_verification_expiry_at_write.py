"""Expiry must be re-enforced against the clock at the consumption write.

The early SELECT in ``verify_email`` is only a fast-fail validity check. The
authoritative single-use decision is the guarded ``UPDATE ... WHERE used = 0
AND expires_at >= ?``. That predicate must be evaluated with a clock read at
the moment of the write, not with the snapshot used by the validation SELECT:
a token that expires between validation and the write must fail closed.

Mirrors the invite-redemption TOCTOU fix in
``src/core/servers/handlers/members/invites.py``.
"""

import uuid
from unittest.mock import patch

from src.core.auth.tokens import parse_token
from src.utils import encryption


def _register(auth_manager, label="expwrite"):
    unique = uuid.uuid4().hex[:8]
    username = f"{label}_{unique}"
    email = f"{label}_{unique}@test.local"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username=username, email=email, password="TestPass123!"
        )
    return user, email


def _fresh_token(auth_manager, email_sender, user, email):
    manager = auth_manager._get_manager()
    manager.email_sender = email_sender
    email_sender.send.reset_mock()
    assert manager._send_verification_email(user.id, email)
    body = email_sender.send.call_args[0][2]
    return body.split("Verification Token: ")[1].strip()


def test_expiry_rechecked_at_consumption_write(db, auth_manager, email_sender):
    manager = auth_manager._get_manager()
    user, email = _register(auth_manager)
    token = _fresh_token(auth_manager, email_sender, user, email)
    tid = parse_token(token)["id"]

    # Test config registers users as verified; model the verification-required
    # state so a wrongful consume would visibly flip the flag.
    db.execute("UPDATE auth_users SET email_verified = 0 WHERE id = ?", (user.id,))

    # Place the token's expiry inside the validation->write window.
    expires_at = manager._get_timestamp() + 5_000
    db.execute(
        "UPDATE auth_email_tokens SET expires_at = ? WHERE id = ?", (expires_at, tid)
    )

    # First clock read (validation SELECT) sees it live; the write-time read
    # sees it expired. A consume write that reuses the stale validation clock
    # would wrongly redeem the token.
    calls = {"n": 0}

    def clock():
        calls["n"] += 1
        return expires_at - 1 if calls["n"] == 1 else expires_at + 1

    with patch.object(manager, "_get_timestamp", side_effect=clock):
        assert auth_manager.verify_email(token) is False

    # Fail closed: token left unconsumed and account still unverified.
    row = db.fetch_one("SELECT used FROM auth_email_tokens WHERE id = ?", (tid,))
    assert row["used"] == 0
    verified = db.fetch_one(
        "SELECT email_verified FROM auth_users WHERE id = ?", (user.id,)
    )
    assert verified["email_verified"] == 0
