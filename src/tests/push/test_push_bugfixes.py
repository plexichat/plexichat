"""
Regression tests for the second push audit fixes.

Covers:
- P3: a push token already bound to one account cannot be re-registered to a
  different account (previously the token silently transferred, letting
  account B hijack account A's notifications).
- P4: Web Push registration requires the subscription ``p256dh``/``auth`` keys,
  and re-registering an existing token never grows the per-user token count
  beyond ``MAX_TOKENS_PER_USER``.
"""

import pytest

from src.core.push.exceptions import PermissionDeniedError


@pytest.fixture
def push_manager_setup(db):
    from src.core.push import setup as push_module_setup
    from src.core.push.manager import PushManager

    push_module_setup(db, notifications_module=None)
    return PushManager(db, notifications_module=None)


def _register_user(auth_manager, pii_gen, name):
    from unittest.mock import patch

    from src.utils import encryption

    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        return auth_manager.register(
            username=name, email=pii_gen.email(), password="TestPass123!"
        )


class TestPushTokenOwnership:
    def test_p3_cross_user_reassignment_rejected(
        self, push_manager_setup, auth_manager, pii_gen
    ):
        user1 = _register_user(auth_manager, pii_gen, "pushowner")
        user2 = _register_user(auth_manager, pii_gen, "pushhijack")

        push_manager_setup.register_token(
            user_id=user1.id, token="shared-fcm-token", platform="android"
        )

        with pytest.raises(PermissionDeniedError):
            push_manager_setup.register_token(
                user_id=user2.id, token="shared-fcm-token", platform="android"
            )

    def test_p3_same_user_reregistration_allowed(
        self, push_manager_setup, auth_manager, pii_gen
    ):
        user1 = _register_user(auth_manager, pii_gen, "pushreuse")
        first = push_manager_setup.register_token(
            user_id=user1.id, token="reuse-token", platform="ios"
        )
        second = push_manager_setup.register_token(
            user_id=user1.id, token="reuse-token", platform="ios"
        )
        assert first["id"] == second["id"]


class TestWebPushRegistration:
    def test_p4_web_registers_without_keys_back_compat(
        self, push_manager_setup, auth_manager, pii_gen
    ):
        user1 = _register_user(auth_manager, pii_gen, "webnokeys")
        record = push_manager_setup.register_token(
            user_id=user1.id, token="https://push.example/ep", platform="web"
        )
        assert record["platform"] == "web"
        # Credentials are never echoed back in API/return payloads.
        assert "p256dh" not in record
        assert "auth" not in record

    def test_p4_web_stores_keys_encrypted_at_rest(
        self, push_manager_setup, auth_manager, pii_gen
    ):
        user1 = _register_user(auth_manager, pii_gen, "webwithkeys")
        record = push_manager_setup.register_token(
            user_id=user1.id,
            token="https://push.example/ep2",
            platform="web",
            p256dh="BPublicKey",
            auth="authsecret",
        )
        # Raw credentials must NOT appear in the returned record.
        assert "token" not in record
        assert "p256dh" not in record
        assert "auth" not in record

        # At rest the row holds ENC envelopes — never plaintext.
        row = push_manager_setup._db.fetch_one(
            "SELECT token, token_index, p256dh, auth FROM push_tokens WHERE id = ?",
            (record["id"],),
        )
        assert row["token"].startswith(("ENC:", "enc:"))
        assert row["p256dh"].startswith(("ENC:", "enc:"))
        assert row["auth"].startswith(("ENC:", "enc:"))
        assert "BPublicKey" not in row["p256dh"]
        assert "authsecret" not in row["auth"]
        assert row["token_index"]

        # Decrypted-on-read semantics for internal delivery use only.
        tokens = push_manager_setup.get_user_tokens(user_id=user1.id)
        assert any(
            t["token"] == "https://push.example/ep2"
            and t["p256dh"] == "BPublicKey"
            and t["auth"] == "authsecret"
            for t in tokens
        )

    def test_p4_reuse_does_not_exceed_max_tokens(
        self, push_manager_setup, auth_manager, pii_gen
    ):
        from src.core.push.manager import PushManager

        user1 = _register_user(auth_manager, pii_gen, "pushmaxreuse")
        for i in range(PushManager.MAX_TOKENS_PER_USER):
            push_manager_setup.register_token(
                user_id=user1.id, token=f"tok-{i}", platform="android"
            )
        # Re-register an existing token (same user) — must not add a row.
        push_manager_setup.register_token(
            user_id=user1.id, token="tok-0", platform="android"
        )
        rows = push_manager_setup.get_user_tokens(user_id=user1.id)
        assert len(rows) == PushManager.MAX_TOKENS_PER_USER
