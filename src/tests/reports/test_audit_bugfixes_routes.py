"""Regression tests for PlexiStash-adjacent report route fixes (RP-1..RP-6).

Exercises the HTTP layer with mocked ``api`` dependencies so the validation
and de-duplication logic runs without a full app bootstrap. The route handlers
are async coroutines, so they are driven via ``asyncio.run``.
"""

import asyncio
from types import SimpleNamespace

import pytest

import src.api as api
from src.api.routes.reports import report_message, report_user
from src.api.schemas.reports import MessageReportRequest, UserReportRequest

CATEGORY = "spam"


class _User:
    def __init__(self, user_id):
        self.user_id = user_id


def _fake_reports(find_message=None, find_user=None):
    created = {}

    def report_message(**kwargs):
        rid = len(created) + 1
        created[rid] = kwargs
        return SimpleNamespace(id=rid)

    def report_user(**kwargs):
        rid = len(created) + 1
        created[rid] = kwargs
        return SimpleNamespace(id=rid)

    return SimpleNamespace(
        report_message=report_message,
        report_user=report_user,
        find_open_message_report=lambda *a, **k: find_message,
        find_open_user_report=lambda *a, **k: find_user,
    )


def _patch(monkeypatch, reports, messaging, db):
    monkeypatch.setattr(api, "get_reports", lambda: reports)
    monkeypatch.setattr(api, "get_messaging", lambda: messaging)
    monkeypatch.setattr(api, "get_db", lambda: db)


async def _call(coro):
    return await coro


def test_report_message_self_report_rejected(monkeypatch):
    msg = SimpleNamespace(id=5, author_id=1, content="hi")
    messaging = SimpleNamespace(get_message=lambda uid, mid: msg)
    _patch(monkeypatch, _fake_reports(), messaging, None)
    with pytest.raises(Exception) as exc:
        asyncio.run(
            report_message(
                MessageReportRequest(
                    message_id="5", channel_id="1", reason="x", category=CATEGORY
                ),
                _User(1),
            )
        )
    assert exc.value.status_code == 400


def test_report_message_inaccessible_rejected(monkeypatch):
    messaging = SimpleNamespace(get_message=lambda uid, mid: None)
    _patch(monkeypatch, _fake_reports(), messaging, None)
    with pytest.raises(Exception) as exc:
        asyncio.run(
            report_message(
                MessageReportRequest(
                    message_id="5", channel_id="1", reason="x", category=CATEGORY
                ),
                _User(2),
            )
        )
    assert exc.value.status_code == 400


def test_report_message_invalid_category_rejected(monkeypatch):
    msg = SimpleNamespace(id=5, author_id=9, content="hi")
    messaging = SimpleNamespace(get_message=lambda uid, mid: msg)
    _patch(monkeypatch, _fake_reports(), messaging, None)
    with pytest.raises(Exception) as exc:
        asyncio.run(
            report_message(
                MessageReportRequest(
                    message_id="5", channel_id="1", reason="x", category="bogus"
                ),
                _User(2),
            )
        )
    assert exc.value.status_code == 400


def test_report_message_dedupe_returns_existing(monkeypatch):
    msg = SimpleNamespace(id=5, author_id=9, content="hi")
    messaging = SimpleNamespace(get_message=lambda uid, mid: msg)
    existing = SimpleNamespace(id=777)
    _patch(monkeypatch, _fake_reports(find_message=existing), messaging, None)
    result = asyncio.run(
        report_message(
            MessageReportRequest(
                message_id="5", channel_id="1", reason="x", category=CATEGORY
            ),
            _User(2),
        )
    )
    assert result.report_id == str(777)


def test_report_message_success(monkeypatch):
    msg = SimpleNamespace(id=5, author_id=9, content="hi")
    messaging = SimpleNamespace(get_message=lambda uid, mid: msg)
    _patch(monkeypatch, _fake_reports(), messaging, None)
    result = asyncio.run(
        report_message(
            MessageReportRequest(
                message_id="5", channel_id="1", reason="x", category=CATEGORY
            ),
            _User(2),
        )
    )
    assert result.success is True


def test_report_user_self_rejected(monkeypatch):
    _patch(monkeypatch, _fake_reports(), None, None)
    with pytest.raises(Exception) as exc:
        asyncio.run(
            report_user(
                UserReportRequest(user_id="2", reason="x", category=CATEGORY), _User(2)
            )
        )
    assert exc.value.status_code == 400


def test_report_user_not_found_rejected(monkeypatch):
    db = SimpleNamespace(
        fetch_one=lambda q, p: None if "auth_users" in q else {"id": 1}
    )
    _patch(monkeypatch, _fake_reports(), None, db)
    with pytest.raises(Exception) as exc:
        asyncio.run(
            report_user(
                UserReportRequest(user_id="123", reason="x", category=CATEGORY),
                _User(2),
            )
        )
    assert exc.value.status_code == 400


def test_report_user_invalid_evidence_rejected(monkeypatch):
    db = SimpleNamespace(fetch_one=lambda q, p: {"id": 123})
    _patch(monkeypatch, _fake_reports(), None, db)
    with pytest.raises(Exception) as exc:
        asyncio.run(
            report_user(
                UserReportRequest(
                    user_id="123",
                    reason="x",
                    category=CATEGORY,
                    evidence_message_ids=["not-a-number"],
                ),
                _User(2),
            )
        )
    assert exc.value.status_code == 400


def test_report_user_invalid_category_rejected(monkeypatch):
    db = SimpleNamespace(fetch_one=lambda q, p: {"id": 123})
    _patch(monkeypatch, _fake_reports(), None, db)
    with pytest.raises(Exception) as exc:
        asyncio.run(
            report_user(
                UserReportRequest(user_id="123", reason="x", category="weird"), _User(2)
            )
        )
    assert exc.value.status_code == 400


def test_report_user_dedupe_returns_existing(monkeypatch):
    db = SimpleNamespace(fetch_one=lambda q, p: {"id": 123})
    existing = SimpleNamespace(id=42)
    _patch(monkeypatch, _fake_reports(find_user=existing), None, db)
    result = asyncio.run(
        report_user(
            UserReportRequest(user_id="123", reason="x", category=CATEGORY), _User(2)
        )
    )
    assert result.report_id == str(42)


def test_report_user_success(monkeypatch):
    db = SimpleNamespace(fetch_one=lambda q, p: {"id": 123})
    _patch(monkeypatch, _fake_reports(), None, db)
    result = asyncio.run(
        report_user(
            UserReportRequest(user_id="123", reason="x", category=CATEGORY), _User(2)
        )
    )
    assert result.success is True
