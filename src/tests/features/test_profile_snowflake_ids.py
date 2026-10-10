"""Profile responses must carry snowflake ids as decimal strings.

``GET /features/users/{user_id}/profile`` and ``PATCH /features/users/@me/profile``
served the profile's ``id`` (the ``user_profiles`` row snowflake) and ``user_id``
as raw integers while every other resource schema uses the string convention. A
JS ``Number`` cannot hold a 64-bit snowflake exactly, so a numeric id would be
rounded by the client and subsequent lookups would miss.
"""

import uuid
from unittest.mock import patch

import pytest

from src.utils import encryption
from src.core import settings as settings_module

PROFILE_URL = "/api/v1/features/users/{user_id}/profile"
UPDATE_URL = "/api/v1/features/users/@me/profile"


def _register_and_login(auth_manager, label="profile_user"):
    unique = uuid.uuid4().hex[:8]
    username = f"{label}_{unique}"
    email = f"{label}_{unique}@test.local"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username=username, email=email, password="TestPass123!"
        )
    with patch.object(encryption, "verify_password", return_value=True):
        result = auth_manager.login(username, "TestPass123!")
    return user, result.token


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def pair(auth_manager):
    alice, alice_token = _register_and_login(auth_manager, "alice")
    bob, bob_token = _register_and_login(auth_manager, "bob")
    return {
        "alice": (alice, alice_token),
        "bob": (bob, bob_token),
    }


class TestProfileSnowflakeIds:
    def test_get_serves_string_snowflake_ids(self, test_client, pair):
        alice, alice_token = pair["alice"]
        bob, _ = pair["bob"]

        resp = test_client.get(
            PROFILE_URL.format(user_id=bob.id), headers=_headers(alice_token)
        )
        assert resp.status_code == 200, resp.text
        profile = resp.json()["profile"]

        assert profile["user_id"] == str(bob.id)
        assert isinstance(profile["user_id"], str)
        # The empty/default profile shape has no row id yet.
        assert profile["id"] is None or isinstance(profile["id"], str)
        # Non-id fields keep their native shape.
        assert isinstance(profile["created_at"], int)

    def test_private_projection_serves_string_user_id(self, test_client, pair):
        alice, alice_token = pair["alice"]
        bob, _ = pair["bob"]
        settings_module.set_setting(bob.id, "profile_visibility", "private")

        resp = test_client.get(
            PROFILE_URL.format(user_id=bob.id), headers=_headers(alice_token)
        )
        assert resp.status_code == 200, resp.text
        profile = resp.json()["profile"]
        assert profile["user_id"] == str(bob.id)
        assert isinstance(profile["user_id"], str)

    def test_update_serves_string_snowflake_ids(self, test_client, pair):
        bob, bob_token = pair["bob"]

        resp = test_client.patch(
            UPDATE_URL,
            headers=_headers(bob_token),
            json={"bio": "hello there"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        profile = body["profile"]

        assert profile["user_id"] == str(bob.id)
        assert isinstance(profile["user_id"], str)
        assert profile["id"] is None or isinstance(profile["id"], str)

    def test_self_read_serves_string_snowflake_ids(self, test_client, pair):
        bob, bob_token = pair["bob"]

        resp = test_client.get(
            PROFILE_URL.format(user_id=bob.id), headers=_headers(bob_token)
        )
        assert resp.status_code == 200, resp.text
        profile = resp.json()["profile"]
        assert profile["user_id"] == str(bob.id)
        assert isinstance(profile["user_id"], str)
