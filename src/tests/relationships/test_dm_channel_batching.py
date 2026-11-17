"""The DM channel listing must not re-query recipients the bulk call covered."""

from typing import Any, cast

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import src.api as api
from src.api.routes.users.channel import ChannelMixin


def _run(bulk):
    messaging = MagicMock()
    messaging.get_dm_channels.return_value = [
        SimpleNamespace(id=1, recipient_id=10),
        SimpleNamespace(id=2, recipient_id=11),
    ]

    auth = MagicMock()
    auth.get_user_profiles_bulk.side_effect = bulk
    auth.get_user.return_value = SimpleNamespace(username="fallback")

    with (
        patch.object(api, "get_messaging", return_value=messaging),
        patch.object(api, "get_auth", return_value=auth),
    ):
        result = cast(Any, ChannelMixin.get_dm_channels).__wrapped__(
            ChannelMixin(), current_user=SimpleNamespace(user_id=1)
        )
    return auth, result


def test_successful_bulk_skips_per_recipient_fallback():
    auth, result = _run(lambda ids: {})

    auth.get_user_profiles_bulk.assert_called_once()
    auth.get_user.assert_not_called()
    assert [item.recipient.username for item in result] == ["User 10", "User 11"]


def test_bulk_failure_keeps_per_recipient_fallback():
    def fail(_ids):
        raise RuntimeError("bulk down")

    auth, result = _run(fail)

    assert auth.get_user.call_count == 2
    assert [item.recipient.username for item in result] == ["fallback", "fallback"]
