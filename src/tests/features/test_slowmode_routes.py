"""HTTP-level tests for the feature slowmode routes.

Pins the authorization gate on ``PUT /features/channels/{id}/slowmode``
(manage-channel permission required) plus validation and persistence of the
configured interval. The thread slowmode routes are covered by the threads
test suite; here we exercise the channel half end-to-end.
"""

import uuid
from unittest.mock import patch

import pytest

from src.utils import encryption

SLOWMODE_URL = "/api/v1/features/channels/{channel_id}/slowmode"


def _register_and_login(auth_manager, label="slowmode_user"):
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


@pytest.fixture
def channel_setup(server_manager, auth_manager):
    """A server (with its owner logged in) plus a plain member."""
    owner, owner_token = _register_and_login(auth_manager, "owner")
    member, member_token = _register_and_login(auth_manager, "member")

    server = server_manager.create_server(
        owner_id=owner.id, name=f"Slowmode Server {uuid.uuid4().hex[:6]}"
    )
    channels = server_manager.get_channels(owner.id, server.id)
    assert channels, "server must have a default channel"
    channel = channels[0]

    server_manager.add_member(server.id, member.id)

    return {
        "server": server,
        "channel": channel,
        "owner": owner,
        "owner_token": owner_token,
        "member": member,
        "member_token": member_token,
    }


class TestChannelSlowmodeAuthorization:
    def test_owner_can_set_and_get_slowmode(self, test_client, channel_setup):
        setup = channel_setup
        url = SLOWMODE_URL.format(channel_id=setup["channel"].id)
        headers = {"Authorization": f"Bearer {setup['owner_token']}"}

        response = test_client.put(url, json={"interval_ms": 5000}, headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is True
        assert body["slowmode_seconds"] == 5

        fetched = test_client.get(url, headers=headers)
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["slowmode_seconds"] == 5

    def test_plain_member_is_forbidden(self, test_client, channel_setup):
        setup = channel_setup
        url = SLOWMODE_URL.format(channel_id=setup["channel"].id)
        headers = {"Authorization": f"Bearer {setup['member_token']}"}

        response = test_client.put(url, json={"interval_ms": 5000}, headers=headers)
        assert response.status_code == 403, response.text

        # The setting must be unchanged.
        owner_headers = {"Authorization": f"Bearer {setup['owner_token']}"}
        fetched = test_client.get(url, headers=owner_headers)
        assert fetched.json()["slowmode_seconds"] == 0

    def test_unauthenticated_is_rejected(self, test_client, channel_setup):
        url = SLOWMODE_URL.format(channel_id=channel_setup["channel"].id)
        response = test_client.put(url, json={"interval_ms": 5000})
        assert response.status_code == 401, response.text


class TestChannelSlowmodeValidation:
    def test_interval_above_maximum_is_rejected(self, test_client, channel_setup):
        setup = channel_setup
        url = SLOWMODE_URL.format(channel_id=setup["channel"].id)
        headers = {"Authorization": f"Bearer {setup['owner_token']}"}

        response = test_client.put(
            url,
            json={"interval_ms": 7 * 3600 * 1000},
            headers=headers,
        )
        assert response.status_code == 400, response.text

    def test_unknown_channel_is_not_found(self, test_client, channel_setup):
        setup = channel_setup
        url = SLOWMODE_URL.format(channel_id=999999999)
        headers = {"Authorization": f"Bearer {setup['owner_token']}"}

        response = test_client.put(url, json={"interval_ms": 5000}, headers=headers)
        assert response.status_code == 404, response.text
