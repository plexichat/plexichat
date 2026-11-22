"""Bot credentials must not act as their owner on PlexiSum import routes.

A bot token's ``user_id`` is its human owner (see
``AuthManager._verify_bot_token`` / ``TokenInfo``); the bot's own account id
lives in ``account_id``. PlexiSum is an owner-scoped, human-only feature and
both import handlers key their manager lookup -- and therefore their
authorization -- on ``current_user.user_id``:

* ``files.import_csv`` -> ``manager.import_csv(current_user.user_id, ...)``
* ``xlsx.import_xlsx`` -> ``manager.import_xlsx(current_user.user_id, ...)``

The manager authorizes those writes by comparing ``user_id`` to the
authoritative ``sum_workbooks.owner_id``. For a bot credential ``user_id`` is
the OWNER, so without an explicit user-token gate a bot passes that owner
check and can overwrite cells in the owner's private workbook. The sibling
export/download routes and the suite feed routes already reject bot tokens
up front with ``require_user_token``; the import routes must do the same.
"""

import asyncio
import inspect

import pytest
from fastapi import HTTPException

import src.api.routes.suite.sum as sum_pkg
from src.api.middleware.authentication import TokenInfo
from src.api.routes.suite.sum import files as files_route
from src.api.routes.suite.sum import xlsx as xlsx_route
from src.api.schemas.sum import SumCsvImportRequest, SumXlsxImportRequest
from src.core.auth.models import AccountType
from src.core.suite.sum.manager import SumPermissionError

pytestmark = [pytest.mark.unit, pytest.mark.security]

_OWNER_ID = 100
_BOT_ID = 500
_WORKBOOK_ID = 1
_SHEET_ID = 2
_CSV_CONTENT = "col,row\nvalue,42\n"


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
    """Owner-scoped manager: only the real owner's user_id may mutate cells.

    Records every accepted mutation so the test can prove the write actually
    landed (rather than merely relying on an exception not being raised).
    """

    def __init__(self):
        self.imports = []

    def _check(self, user_id):
        if int(user_id) != _OWNER_ID:
            raise SumPermissionError("not the workbook owner")

    def import_csv(self, user_id, workbook_id, sheet_id, content, base_revision=None):
        self._check(user_id)
        self.imports.append(("csv", int(user_id), int(workbook_id), int(sheet_id)))
        return {
            "operation_id": "csv-import-1",
            "revision": 1,
            "rows": 2,
            "cells": 4,
        }

    def import_xlsx(self, user_id, workbook_id, sheet_id, content_b64, base_revision):
        self._check(user_id)
        self.imports.append(("xlsx", int(user_id), int(workbook_id), int(sheet_id)))
        return {
            "operation_id": "xlsx-import-1",
            "revision": 1,
            "rows": 2,
            "cells": 4,
        }


@pytest.fixture
def manager(monkeypatch):
    fake = _FakeManager()
    monkeypatch.setattr(sum_pkg, "_manager", lambda: fake)
    return fake


def _call(method, **kwargs):
    result = method(**kwargs)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


def test_owner_user_token_can_import(manager):
    """Non-vacuous: the human owner genuinely lands a CSV mutation."""
    result = _call(
        files_route.import_csv,
        workbook_id=str(_WORKBOOK_ID),
        sheet_id=str(_SHEET_ID),
        body=SumCsvImportRequest(content=_CSV_CONTENT, base_revision=0),
        current_user=_token(_OWNER_ID),
    )
    assert result.cells == 4
    assert manager.imports == [("csv", _OWNER_ID, _WORKBOOK_ID, _SHEET_ID)]


def test_other_user_cannot_import_owner_workbook(manager):
    with pytest.raises(HTTPException) as exc:
        _call(
            files_route.import_csv,
            workbook_id=str(_WORKBOOK_ID),
            sheet_id=str(_SHEET_ID),
            body=SumCsvImportRequest(content=_CSV_CONTENT, base_revision=0),
            current_user=_token(_OWNER_ID + 1),
        )
    assert exc.value.status_code == 403
    assert manager.imports == []


@pytest.mark.parametrize(
    "handler, body, kwargs, label",
    [
        (
            files_route.import_csv,
            SumCsvImportRequest(content=_CSV_CONTENT, base_revision=0),
            {"workbook_id": str(_WORKBOOK_ID), "sheet_id": str(_SHEET_ID)},
            "csv",
        ),
        (
            xlsx_route.import_xlsx,
            SumXlsxImportRequest(content="AAAA", base_revision=0),
            {"workbook_id": str(_WORKBOOK_ID), "sheet_id": str(_SHEET_ID)},
            "xlsx",
        ),
    ],
    ids=["import_csv", "import_xlsx"],
)
def test_bot_token_cannot_mutate_owner_workbook(manager, handler, body, kwargs, label):
    """A bot shares the owner's user_id but must still be refused before the write."""
    bot = _token(_OWNER_ID, bot=True)
    with pytest.raises(HTTPException) as exc:
        _call(handler, current_user=bot, body=body, **kwargs)
    assert exc.value.status_code == 403
    # The write must never reach the manager -- the gate is fail-closed.
    assert manager.imports == [], f"bot token landed a {label} import"
