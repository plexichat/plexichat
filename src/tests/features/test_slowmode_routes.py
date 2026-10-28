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
        # Snowflake ids cross the wire as strings: a JS Number cannot
        # hold a 64-bit id exactly, so a numeric channel_id would be
        # rounded by the client and subsequent lookups would miss.
        assert body["channel_id"] == str(setup["channel"].id)
        assert isinstance(body["channel_id"], str)

        fetched = test_client.get(url, headers=headers)
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["slowmode_seconds"] == 5
        assert fetched.json()["channel_id"] == str(setup["channel"].id)
        assert isinstance(fetched.json()["channel_id"], str)

    def test_setting_slowmode_invalidates_the_channel_cache(
        self, test_client, channel_setup, server_manager
    ):
        """Readers of get_channel() must see the new interval immediately.

        The route used to write SQL directly, leaving the cached Channel
        (60s TTL) reporting slowmode_seconds=0 -- which is exactly what the
        send path consults, so slowmode stayed unenforced after enabling it.
        """
        setup = channel_setup
        channel_id = setup["channel"].id

        # Warm the cache with the pre-change row.
        assert (
            server_manager.get_channel(channel_id, setup["owner"].id).slowmode_seconds
            == 0
        )

        response = test_client.put(
            SLOWMODE_URL.format(channel_id=channel_id),
            json={"interval_ms": 5000},
            headers={"Authorization": f"Bearer {setup['owner_token']}"},
        )
        assert response.status_code == 200, response.text

        assert (
            server_manager.get_channel(channel_id, setup["owner"].id).slowmode_seconds
            == 5
        )

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


class TestThreadSlowmodeSnowflakeSerialization:
    def test_get_slowmode_stringifies_ids(self, monkeypatch):
        """Thread slowmode ids must cross the wire as strings.

        A JS Number cannot represent a 64-bit snowflake exactly, so a
        numeric ``thread_id``/``updated_by`` would be rounded by the
        client. ``interval_ms``/``updated_at`` stay numeric.
        """
        from unittest.mock import MagicMock

        from src.core.threads.slowmode import ThreadSlowmode

        monkeypatch.setattr("src.core.threads.slowmode.cache_get", lambda *a, **k: None)
        monkeypatch.setattr("src.core.threads.slowmode.cache_set", lambda *a, **k: None)

        db = MagicMock()
        db.fetch_one.return_value = {
            "slowmode_interval_ms": 5000,
            "slowmode_updated_by": 1234567890123456789,
            "slowmode_updated_at": 1700000000000,
        }
        result = ThreadSlowmode(db).get_slowmode(1234567890123456789)

        assert result["thread_id"] == "1234567890123456789"
        assert isinstance(result["thread_id"], str)
        assert result["updated_by"] == "1234567890123456789"
        assert isinstance(result["updated_by"], str)
        assert result["interval_ms"] == 5000
        assert result["updated_at"] == 1700000000000

    def test_set_slowmode_stringifies_ids(self):
        """The set path must return the same string-id shape as the get path."""
        from unittest.mock import MagicMock

        from src.core.threads.slowmode import ThreadSlowmode

        db = MagicMock()
        db.fetch_one.return_value = {"id": 1234567890123456789}
        result = ThreadSlowmode(db).set_slowmode(
            1234567890123456789, 5000, 9876543210987654321, can_manage=True
        )

        assert result["thread_id"] == "1234567890123456789"
        assert isinstance(result["thread_id"], str)
        assert result["updated_by"] == "9876543210987654321"
        assert isinstance(result["updated_by"], str)
        assert result["interval_ms"] == 5000
