"""Bot credentials must not act as their owner on transcript exports.

A bot token's ``user_id`` is its human owner; the bot's own account id lives
in ``account_id`` (see ``AuthManager._verify_bot_token`` / ``TokenInfo``).
Every transcript-export handler keys its read/write path -- and its final
"ownership" check -- on ``current_user.user_id``:

* ``request_transcript_export`` stores the export under that id;
* ``get_export_status`` accepts a status row when
  ``status["user_id"] == current_user.user_id``;
* ``download_transcript_export`` locates the file with that id and repeats
  the same ownership check.

For a bot credential ``user_id`` is the OWNER, so a bot passes the ownership
gate on the owner's private transcript exports and can download them. This is
an owner-scoped, human-only feature (like DMs, reports and storage quota), so
bot credentials are rejected up front with ``require_user_token``.
"""

import asyncio
import inspect

import pytest
from fastapi import HTTPException

import src.api as api
import src.api.routes.messages.export as export_route
import src.core.messaging.services.export as export_service
from src.api.middleware.authentication import TokenInfo
from src.api.routes.users.helpers import require_user_token
from src.api.schemas.messages import TranscriptExportRequest
from src.core.auth.models import AccountType

pytestmark = [pytest.mark.unit, pytest.mark.security]

_OWNER_ID = 100
_BOT_ID = 500
_EXPORT_ID = "export_owner_transcript"


def _token(uid, *, bot=False):
    return TokenInfo(
        valid=True,
        token_type="bot" if bot else "user",
        account_id=_BOT_ID if bot else uid,
        user_id=uid,
        session_id=None,
        permissions={},
        rate_limit_tier="bot" if bot else "standard",
        expires_at=None,
        username="tester",
        account_type=AccountType.BOT if bot else AccountType.USER,
    )


def _call(method, **kwargs):
    result = method(**kwargs)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


@pytest.fixture
def owner_export(monkeypatch):
    """A completed export owned by the human, as the service would return it."""
    secret_url = "https://exports.invalid/owner-private-transcript.json"

    class _FakeExportService:
        def __init__(self, *args, **kwargs):
            pass

        def get_export_status(self, export_id):
            return {
                "export_id": export_id,
                "status": "completed",
                "message_count": 9,
                "file_url": secret_url,
                "expires_at": 2**40,
                "error": None,
                "user_id": _OWNER_ID,
            }

    class _DB:
        def close(self):
            pass

    monkeypatch.setattr(api, "get_db", lambda: _DB())
    monkeypatch.setattr(export_service, "TranscriptExportService", _FakeExportService)
    monkeypatch.setattr(
        "src.core.messaging.repositories.message.MessageRepository", lambda db: object()
    )
    monkeypatch.setattr(
        "src.core.messaging.repositories.participant.ParticipantRepository",
        lambda db: object(),
    )
    return secret_url


def test_owner_can_read_own_export_status(owner_export):
    """Non-vacuous: the export is genuinely the human owner's private data."""
    result = _call(
        export_route.get_export_status,
        channel_id="1",
        export_id=_EXPORT_ID,
        current_user=_token(_OWNER_ID),
    )
    assert result.file_url == owner_export


def test_other_user_cannot_read_owner_export_status(owner_export):
    with pytest.raises(HTTPException) as exc:
        _call(
            export_route.get_export_status,
            channel_id="1",
            export_id=_EXPORT_ID,
            current_user=_token(_OWNER_ID + 1),
        )
    assert exc.value.status_code == 404


def test_bot_token_cannot_read_owner_export_status(owner_export):
    """A bot shares the owner's user_id but must still be refused."""
    bot = _token(_OWNER_ID, bot=True)
    with pytest.raises(HTTPException) as exc:
        _call(
            export_route.get_export_status,
            channel_id="1",
            export_id=_EXPORT_ID,
            current_user=bot,
        )
    assert exc.value.status_code == 403


@pytest.mark.parametrize(
    "handler, kwargs",
    [
        (
            export_route.request_transcript_export,
            {"channel_id": "1", "body": TranscriptExportRequest()},
        ),
        (
            export_route.get_export_status,
            {"channel_id": "1", "export_id": _EXPORT_ID},
        ),
        (
            export_route.download_transcript_export,
            {"channel_id": "1", "export_id": _EXPORT_ID},
        ),
    ],
)
def test_bot_token_rejected_before_any_export_service_access(
    monkeypatch, handler, kwargs
):
    def tripwire(*args, **kwargs):
        raise AssertionError("export service was accessed for a bot credential")

    monkeypatch.setattr(api, "get_messaging", tripwire)
    monkeypatch.setattr(api, "get_db", tripwire)

    with pytest.raises(HTTPException) as exc:
        _call(handler, current_user=_token(_OWNER_ID, bot=True), **kwargs)
    assert exc.value.status_code == 403


def test_user_token_still_passes_the_guard():
    assert require_user_token(_token(_OWNER_ID)) is None
