"""Regression tests for presence route bug fixes (P1, P2).

P1: a status-only presence update must NOT wipe an existing custom status.
P2: an unknown/invalid user must return 404, not 500.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration

from src.api.schemas.presence import PresenceUpdate
from src.core.auth.models import AccountType, TokenInfo
from src.core.presence.exceptions import UserNotFoundError


def _token_for(user):
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=user.id,
        user_id=user.id,
        session_id=1,
        permissions={},
        rate_limit_tier="standard",
        expires_at=None,
        username=user.username,
        account_type=AccountType.USER,
    )


class TestPresenceRouteP1P2:
    def test_status_only_preserves_custom_status(
        self, presence_manager, auth_manager, pii_gen
    ):
        import unittest.mock as umock

        from src.utils import encryption
        from src.api.routes.presence import update_presence

        with umock.patch.object(
            encryption, "hash_password", return_value="fake_hash_$test"
        ):
            user = auth_manager.register(
                username="p1statusonly",
                email=pii_gen.email(),
                password="TestPass123!",
            )

        presence_manager.set_custom_status(
            user_id=user.id, text="On vacation", emoji="🏖️"
        )

        token = _token_for(user)
        with umock.patch(
            "src.api.routes.presence.api.get_presence", return_value=presence_manager
        ):
            resp = asyncio.run(update_presence(PresenceUpdate(status="idle"), token))

        # P1: status-only update must preserve the previously-set custom status.
        assert resp.custom_status == "On vacation"
        assert resp.custom_emoji == "🏖️"
        assert resp.status == "idle"

    def test_unknown_user_returns_404(self, presence_manager, auth_manager, pii_gen):
        import unittest.mock as umock

        from fastapi import HTTPException
        from src.utils import encryption
        from src.api.routes.presence import update_presence

        with umock.patch.object(
            encryption, "hash_password", return_value="fake_hash_$test"
        ):
            user = auth_manager.register(
                username="p2unknown",
                email=pii_gen.email(),
                password="TestPass123!",
            )

        token = _token_for(user)
        with umock.patch(
            "src.api.routes.presence.api.get_presence", return_value=presence_manager
        ):
            with umock.patch.object(
                presence_manager,
                "set_status",
                side_effect=UserNotFoundError("user vanished"),
            ):
                with pytest.raises(HTTPException) as exc_info:
                    asyncio.run(update_presence(PresenceUpdate(status="online"), token))

        # P2: NotFound must surface as 404, never 500.
        assert exc_info.value.status_code == 404
