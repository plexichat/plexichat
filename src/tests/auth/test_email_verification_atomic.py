"""M3: email-verification tokens are single-use and atomically consumed.

First verify succeeds, second fails; concurrent verifies let exactly one win.
"""

import threading
import uuid
from unittest.mock import patch

from src.utils import encryption


def _register(auth_manager, label="m3atomic"):
    unique = uuid.uuid4().hex[:8]
    username = f"{label}_{unique}"
    email = f"{label}_{unique}@test.local"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username=username, email=email, password="TestPass123!"
        )
    return user, email


def _fresh_token(auth_manager, email_sender, user, email):
    auth_manager._get_manager().email_sender = email_sender
    email_sender.send.reset_mock()
    assert auth_manager._get_manager()._send_verification_email(user.id, email)
    body = email_sender.send.call_args[0][2]
    return body.split("Verification Token: ")[1].strip()


class TestEmailVerificationAtomic:
    def test_double_verify_second_fails(self, db, auth_manager, email_sender):
        user, email = _register(auth_manager)
        token = _fresh_token(auth_manager, email_sender, user, email)

        assert auth_manager.verify_email(token) is True
        assert auth_manager.verify_email(token) is False

        row = db.fetch_one(
            "SELECT used FROM auth_email_tokens WHERE user_id = ?", (user.id,)
        )
        assert row["used"] == 1

    def test_concurrent_verify_only_one_wins(self, db, auth_manager, email_sender):
        user, email = _register(auth_manager, "m3conc")
        token = _fresh_token(auth_manager, email_sender, user, email)

        barrier = threading.Barrier(8)
        results = []

        def worker():
            try:
                barrier.wait(timeout=10)
            except Exception:
                pass
            results.append(auth_manager.verify_email(token))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert len(results) == 8
        assert sum(1 for r in results if r) == 1

    def test_expired_token_not_consumed(self, db, auth_manager, email_sender):
        import time

        user, email = _register(auth_manager, "m3exp")
        token = _fresh_token(auth_manager, email_sender, user, email)
        from src.core.auth.tokens import parse_token

        tid = parse_token(token)["id"]
        db.execute(
            "UPDATE auth_email_tokens SET expires_at = ? WHERE id = ?",
            (int(time.time() * 1000) - 1000, tid),
        )
        assert auth_manager.verify_email(token) is False
