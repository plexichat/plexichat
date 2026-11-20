"""Report-status mutations require a human user token.

A bot token carries its owner's ``user_id``. The admin-membership gate on
``PATCH /reports/{id}/status`` would therefore accept an admin-owned bot
credential and let it change moderation state. Every other handler in the
feature reports router is bound to a human account via ``require_user_token``;
this endpoint must be too.
"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.routes.features import reports as reports_route
from src.api.routes.features.reports import (
    ReportStatusUpdateRequest,
    update_report_status,
)


def test_bot_token_rejected_before_database_access(monkeypatch):
    def no_db():
        raise AssertionError("database must not be touched for a bot credential")

    monkeypatch.setattr(reports_route.api, "get_db", no_db)

    bot_owner = SimpleNamespace(user_id=4242, token_type="bot")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            update_report_status(
                1,
                ReportStatusUpdateRequest(status="resolved"),
                bot_owner,
            )
        )

    assert exc.value.status_code == 403
    assert "user token" in exc.value.detail["error"]["message"]
