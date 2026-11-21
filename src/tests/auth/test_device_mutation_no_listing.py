"""Device mutations must not hydrate the full device list to do their work.

Every rename/revoke/trust/untrust handler used to go through
``_get_devices(user_id)``, which issues ``SELECT * FROM auth_devices WHERE
user_id = ?`` and decrypts every device's fingerprint, name and type — even
though none of those handlers ever reads the returned rows. That is one wasted
round trip plus O(devices) wasted decryptions on every write. The listing
endpoint still needs the rows; the writes only need the auth manager.
"""

import asyncio
import inspect
from unittest.mock import patch

import pytest

from src.api.middleware.authentication import TokenInfo
from src.api.routes.auth import devices as routes
from src.api.schemas.common import SnowflakeID
from src.api.schemas.policy_types import UIText
from src.core.auth.models import AccountType


def _user():
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=SnowflakeID("100"),
        user_id=SnowflakeID("100"),
        session_id=None,
        permissions={},
        rate_limit_tier="standard",
        expires_at=None,
        username="tester",
        account_type=AccountType.USER,
    )


def _call(method, **kwargs):
    result = method(**kwargs)
    return asyncio.run(result) if inspect.isawaitable(result) else result


class CountingAuth:
    """Counts the wasted list read and records the single intended mutation."""

    def __init__(self):
        self.get_devices_calls = 0
        self.get_devices_users = []
        self.mutations = []

    def get_devices(self, user_id):
        self.get_devices_calls += 1
        self.get_devices_users.append(user_id)
        return []

    def rename_device(self, user_id, device_id, name):
        self.mutations.append(("rename", user_id, device_id))
        return True

    def revoke_device(self, user_id, device_id):
        self.mutations.append(("revoke", user_id, device_id))
        return True

    def trust_device(self, user_id, fingerprint):
        self.mutations.append(("trust", user_id, fingerprint))
        return True

    def untrust_device(self, user_id, fingerprint):
        self.mutations.append(("untrust", user_id, fingerprint))
        return True


_RENAME_BODY = routes.RenameDeviceRequest(name=UIText("Laptop"))
_TRUST_BODY = routes.TrustDeviceRequest(fingerprint="fp-12345678")


@pytest.mark.parametrize(
    "handler,kwargs,expected_mutation",
    [
        (
            "rename_device",
            {"device_id": "1", "body": _RENAME_BODY},
            ("rename", "100", 1),
        ),
        ("revoke_device", {"device_id": "1"}, ("revoke", "100", 1)),
        ("trust_device", {"body": _TRUST_BODY}, ("trust", "100", "fp-12345678")),
        ("untrust_device", {"body": _TRUST_BODY}, ("untrust", "100", "fp-12345678")),
    ],
)
def test_device_mutation_does_not_fetch_device_list(handler, kwargs, expected_mutation):
    auth = CountingAuth()
    with patch("src.api.get_auth", return_value=auth):
        _call(getattr(routes, handler), current_user=_user(), **kwargs)

    assert auth.get_devices_calls == 0, "mutation must not hydrate the device list"
    assert auth.mutations == [expected_mutation]


def test_list_devices_still_fetches_device_list():
    """The optimization must not change what the actual listing endpoint does."""
    auth = CountingAuth()
    with patch("src.api.get_auth", return_value=auth):
        result = _call(routes.list_devices, current_user=_user())

    assert auth.get_devices_calls == 1
    assert auth.get_devices_users == ["100"]
    assert auth.mutations == []
    assert result == []
