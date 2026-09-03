"""Regression tests: the attachment-upload route enforces the per-user tier
upload cap server-side.

The client pre-checks the tier cap at file selection (app.js), but that is
advisory only — a crafted client can POST raw multipart and skip it entirely.
The route must apply ``_get_upload_limit(user_id)`` itself and answer 413
before spooling the body, or low-tier accounts can bypass their quota.
"""

import pytest

import src.api as api_module
from src.api.routes.channels.attachments import ChannelAttachmentsMixin
from src.api.routes.channels.helpers import _get_upload_limit
from src.core.auth.models import AccountType, TokenInfo

from fastapi import HTTPException

pytestmark = [pytest.mark.asyncio]


def _token_info(user_id, username):
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=user_id,
        user_id=user_id,
        session_id=None,
        permissions={},
        rate_limit_tier="default",
        expires_at=None,
        username=username,
        account_type=AccountType.USER,
    )


class _FakeUploadFile:
    """Minimal UploadFile stand-in carrying an oversized declared size."""

    def __init__(self, size):
        self.size = size
        self.filename = "big.bin"
        self.content_type = "application/octet-stream"


class TestAttachmentTierCap:
    async def test_oversized_upload_rejected_413_before_spool(self, monkeypatch):
        """A declared size above the user's tier cap must 413 without
        touching media storage."""
        user_id = 4242

        # Tier cap for this user: 10 MiB (config default branch).
        async def fake_get_media():
            return object()  # must be non-None to pass the module guard

        monkeypatch.setattr(api_module, "get_media", fake_get_media)
        monkeypatch.setattr(api_module, "_servers", None)
        monkeypatch.setattr(api_module, "_messaging", None)

        limit = _get_upload_limit(user_id)
        assert limit > 0

        mixin = ChannelAttachmentsMixin()
        big_file = _FakeUploadFile(size=limit + 1)

        with pytest.raises(HTTPException) as exc_info:
            await mixin._upload_attachment(
                "123",  # channel id is never reached: cap check fires first
                big_file,
                current_user=_token_info(user_id, "bigfile"),
            )

        assert exc_info.value.status_code == 413

    async def test_within_limit_passes_cap_check(self, monkeypatch):
        """A file at or below the cap must NOT fail with 413 (it may fail
        later on channel resolution, which this wiring stubs out)."""
        user_id = 4243

        async def fake_get_media():
            return object()

        monkeypatch.setattr(api_module, "get_media", fake_get_media)
        # No servers, no messaging: the route 404s on unknown channel AFTER
        # the cap check — that is the observable ordering we pin.
        monkeypatch.setattr(api_module, "_servers", None)
        monkeypatch.setattr(api_module, "_messaging", None)

        limit = _get_upload_limit(user_id)
        mixin = ChannelAttachmentsMixin()
        ok_file = _FakeUploadFile(size=limit)

        with pytest.raises(HTTPException) as exc_info:
            await mixin._upload_attachment(
                "999999",
                ok_file,
                current_user=_token_info(user_id, "okfile"),
            )

        # Cap accepted the file; the failure is channel resolution (404),
        # never the 413 tier rejection.
        assert exc_info.value.status_code == 404
