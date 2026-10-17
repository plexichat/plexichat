"""Regression tests for the push-token ``token_hint`` derivation.

``_sanitize_token_record`` runs on two record shapes:

* ``register_token`` hands it the raw ``push_tokens`` row, where the
  credentials are still wrapped in their ``ENC:v1:`` envelope;
* ``get_user_tokens`` hands it a record whose credentials were already
  decrypted for delivery.

The hint is documented as "the last 4 characters of the token so users
can tell devices apart". Hashing it off the *stored* value means the
register path published the tail of the ciphertext envelope instead:
AES-256-GCM draws a fresh 12-byte nonce per call, so that value changes
on every encrypt and says nothing about the device. These tests pin the
hint to the plaintext on both paths.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

PLATFORM = "android"
TOKEN = "fcm-device-token-7f3a91c4"


@pytest.fixture
def push_manager(db):
    from src.core.push import setup
    from src.core.push.manager import PushManager

    setup(db, notifications_module=None)
    return PushManager(db, notifications_module=None)


@pytest.fixture
def push_user(db, auth_manager, pii_gen):
    from unittest.mock import patch

    from src.utils import encryption

    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        return auth_manager.register(
            username="pushhint",
            email=pii_gen.email(),
            password="TestPass123!",
        )


def _register(push_manager, user_id: int, token: str = TOKEN) -> dict:
    record = push_manager.register_token(
        user_id=user_id, token=token, platform=PLATFORM
    )
    assert record is not None
    return record


class TestTokenHintDerivation:
    def test_register_hint_matches_the_token_tail(self, push_manager, push_user):
        record = _register(push_manager, push_user.id)
        assert record["token_hint"] == f"…{TOKEN[-4:]}"

    def test_list_hint_matches_the_token_tail(self, push_manager, push_user):
        _register(push_manager, push_user.id)
        listed = push_manager.get_user_tokens(push_user.id)
        assert len(listed) == 1
        hint = push_manager._sanitize_token_record(listed[0])["token_hint"]
        assert hint == f"…{TOKEN[-4:]}"

    def test_register_hint_is_not_the_envelope_tail(self, push_manager, push_user, db):
        _register(push_manager, push_user.id)
        row = db.fetch_one(
            "SELECT token FROM push_tokens WHERE user_id = ?", (push_user.id,)
        )
        assert row is not None
        stored = row["token"]
        assert stored.startswith("ENC:")
        # The stored blob is enveloped, and a hint derived from it would be
        # both meaningless and different from the real one.
        assert push_manager._sanitize_token_record(dict(row))["token_hint"] == (
            f"…{TOKEN[-4:]}"
        )
        assert f"…{str(stored)[-4:]}" != f"…{TOKEN[-4:]}"

    def test_hint_survives_re_registration(self, push_manager, push_user):
        # Re-registering refreshes the envelope; the hint must stay stable
        # so the user can still recognise the device in the list.
        first = _register(push_manager, push_user.id)["token_hint"]
        second = _register(push_manager, push_user.id)["token_hint"]
        assert first == second == f"…{TOKEN[-4:]}"

    def test_raw_credentials_never_survive_sanitising(
        self, push_manager, push_user, db
    ):
        _register(push_manager, push_user.id)
        row = db.fetch_one(
            "SELECT * FROM push_tokens WHERE user_id = ?", (push_user.id,)
        )
        sanitized = push_manager._sanitize_token_record(dict(row))
        for field in ("token", "p256dh", "auth", "token_index"):
            assert field not in sanitized
        assert TOKEN not in str(sanitized)
        assert sanitized["token_hint"] == f"…{TOKEN[-4:]}"
