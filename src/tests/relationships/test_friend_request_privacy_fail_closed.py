"""Security checks must fail CLOSED when the privacy lookup errors.

``_recipient_accepts_friend_requests`` guards the recipient's opt-out from
incoming friend requests. Returning ``True`` on a settings error silently
delivered a request to someone who may have opted out. Every error path must
now deny, while an unset flag (no opt-out recorded) still allows.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes.relationships import friend_requests as fr_route
from src.api.routes.relationships.friend_requests import (
    FriendRequestMixin,
    _recipient_accepts_friend_requests,
)
from src.api.schemas.relationships import FriendRequestCreate


@pytest.mark.relationships
class TestRecipientPrivacyFailClosed:
    def test_get_settings_raising_denies(self):
        with patch.object(
            fr_route.api, "get_settings", side_effect=RuntimeError("settings down")
        ):
            assert _recipient_accepts_friend_requests(123) is False

    def test_settings_module_unavailable_denies(self):
        with patch.object(fr_route.api, "get_settings", return_value=None):
            assert _recipient_accepts_friend_requests(123) is False

    def test_lookup_raising_denies(self):
        settings = MagicMock()
        settings.get.side_effect = RuntimeError("read failed")
        with patch.object(fr_route.api, "get_settings", return_value=settings):
            assert _recipient_accepts_friend_requests(123) is False

    def test_module_facade_reader_is_supported(self):
        """Production wires either the manager (``get``) or module facade."""
        facade = SimpleNamespace(get_setting=lambda uid, key: "false")
        with patch.object(fr_route.api, "get_settings", return_value=facade):
            assert _recipient_accepts_friend_requests(123) is False

    def test_unset_flag_still_allows(self):
        settings = MagicMock()
        settings.get.return_value = None
        with patch.object(fr_route.api, "get_settings", return_value=settings):
            assert _recipient_accepts_friend_requests(123) is True

    def test_opted_out_flag_denies(self):
        settings = MagicMock()
        settings.get.return_value = "false"
        with patch.object(fr_route.api, "get_settings", return_value=settings):
            assert _recipient_accepts_friend_requests(123) is False

    def test_route_denies_when_privacy_lookup_errors(self):
        """A settings error must produce the same 403 as a real opt-out."""
        relationships = MagicMock()
        handler = FriendRequestMixin()
        handler._dispatch_relationship_event = AsyncMock()
        handler._invalidate_relationship_list_cache = MagicMock()

        with (
            patch.object(fr_route.api, "get_relationships", return_value=relationships),
            patch.object(fr_route.api, "get_auth", return_value=None),
            patch.object(
                fr_route.api,
                "get_settings",
                side_effect=RuntimeError("settings down"),
            ),
            patch.object(
                fr_route, "_check_friend_request_rate_limit", return_value=None
            ),
            pytest.raises(HTTPException) as exc_info,
        ):
            import asyncio

            asyncio.run(
                handler.create_relationship(
                    FriendRequestCreate(user_id=910000123),
                    SimpleNamespace(user_id=42),
                )
            )

        assert exc_info.value.status_code == 403
        relationships.send_friend_request.assert_not_called()
