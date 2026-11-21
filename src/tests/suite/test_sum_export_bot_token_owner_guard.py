"""Bot credentials must not act as their owner on PlexiSum export/download routes.

A bot token's ``user_id`` is its human owner; the bot's own account id lives in
``account_id`` (see ``AuthManager._verify_bot_token`` / ``TokenInfo``). Every
workbook export/download handler keys its manager lookup -- and therefore its
authorization -- on ``current_user.user_id``:

* ``download_file`` -> ``manager.download_file(current_user.user_id, ...)``
* ``export_csv``     -> ``manager.export_csv(current_user.user_id, ...)``
* ``export_xlsx``    -> ``manager.export_xlsx(current_user.user_id, ...)``

The manager authorizes those reads by comparing ``user_id`` to the authoritative
``sum_workbooks.owner_id``. For a bot credential ``user_id`` is the OWNER, so a
bot passes that owner check and is handed the owner's private workbook bytes.
This is an owner-scoped, human-only feature (the sibling suite feed routes are
already gated with ``require_user_token``), so bot credentials are rejected up
front.
"""

import asyncio
import inspect

import pytest
from fastapi import HTTPException

import src.api.routes.suite.sum as sum_pkg
from src.api.middleware.authentication import TokenInfo
from src.api.routes.suite.sum import files as files_route
from src.api.routes.suite.sum import xlsx as xlsx_route
from src.core.auth.models import AccountType
from src.core.suite.sum.manager import SumPermissionError

pytestmark = [pytest.mark.unit, pytest.mark.security]

_OWNER_ID = 100
_BOT_ID = 500
_WORKBOOK_ID = 1
_SHEET_ID = 2
_SECRET_BYTES = "cHJpdmF0ZS1vd25lci13b3JrYm9vay1ieXRlcw=="  # owner-only data


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


class _FakeManager:
    """Minimal owner-scoped manager: the caller's user_id must be the owner."""

    def _check(self, user_id):
        if int(user_id) != _OWNER_ID:
            raise SumPermissionError("not the workbook owner")

    def download_file(self, user_id, workbook_id):
        self._check(user_id)
        return {
            "workbook_id": str(workbook_id),
            "filename": "private-workbook.plexisum",
            "content": _SECRET_BYTES,
            "size": len(_SECRET_BYTES),
        }

    def export_csv(self, user_id, workbook_id, sheet_id):
        self._check(user_id)
        return {
            "workbook_id": str(workbook_id),
            "sheet_id": str(sheet_id),
            "filename": "private-workbook.csv",
            "content": "secret,owner,data",
        }

    def export_xlsx(self, user_id, workbook_id, sheet):
        self._check(user_id)
        return {
            "workbook_id": str(workbook_id),
            "filename": "private-workbook.xlsx",
            "content": _SECRET_BYTES,
            "size": len(_SECRET_BYTES),
        }


@pytest.fixture
def owner_workbook(monkeypatch):
    monkeypatch.setattr(sum_pkg, "_manager", lambda: _FakeManager())
    return _SECRET_BYTES


def _call(method, **kwargs):
    result = method(**kwargs)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


def test_owner_can_download_own_workbook_file(owner_workbook):
    """Non-vacuous: the bytes are genuinely the human owner's private file."""
    result = _call(
        files_route.download_file,
        workbook_id=str(_WORKBOOK_ID),
        current_user=_token(_OWNER_ID),
    )
    assert result.content == owner_workbook


def test_other_user_cannot_download_owner_workbook_file(owner_workbook):
    with pytest.raises(HTTPException) as exc:
        _call(
            files_route.download_file,
            workbook_id=str(_WORKBOOK_ID),
            current_user=_token(_OWNER_ID + 1),
        )
    assert exc.value.status_code == 403


@pytest.mark.parametrize(
    "handler, kwargs",
    [
        (
            files_route.download_file,
            {"workbook_id": str(_WORKBOOK_ID)},
        ),
        (
            files_route.export_csv,
            {"workbook_id": str(_WORKBOOK_ID), "sheet_id": str(_SHEET_ID)},
        ),
        (
            xlsx_route.export_xlsx,
            {"workbook_id": str(_WORKBOOK_ID), "sheet_id": None},
        ),
    ],
    ids=["download_file", "export_csv", "export_xlsx"],
)
def test_bot_token_cannot_read_owner_export(owner_workbook, handler, kwargs):
    """A bot shares the owner's user_id but must still be refused."""
    bot = _token(_OWNER_ID, bot=True)
    with pytest.raises(HTTPException) as exc:
        _call(handler, current_user=bot, **kwargs)
    assert exc.value.status_code == 403
