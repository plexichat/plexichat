"""Message-report moderation context must be server-authoritative.

A reporter who can read a message can still lie about which channel/server it
lives in. ``POST /reports/messages`` used to persist the caller-supplied
``channel_id``/``server_id`` verbatim, so a report could be planted in an
arbitrary moderation queue — including one the reporter is not a member of.
The enhanced submission path already resolves this context from the message's
conversation; the basic path must do the same.
"""

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import src.api as api
from src.api.routes.reports import report_message
from src.api.schemas.reports import MessageReportRequest


class _User:
    def __init__(self, user_id):
        self.user_id = user_id


def _body(channel_id: int, server_id: int | None = None) -> MessageReportRequest:
    return MessageReportRequest(
        message_id=cast(Any, "5"),
        channel_id=cast(Any, str(channel_id)),
        reason="spam",
        category="spam",
        details=None,
        server_id=cast(Any, str(server_id)) if server_id is not None else None,
    )


def _patch(monkeypatch, msg, servers=None):
    created = {}

    def fake_report_message(**kwargs):
        created.update(kwargs)
        return SimpleNamespace(id=1)

    reports = SimpleNamespace(
        report_message=fake_report_message,
        find_open_message_report=lambda *a, **k: None,
    )
    messaging = SimpleNamespace(get_message=lambda uid, mid: msg)
    monkeypatch.setattr(api, "get_reports", lambda: reports)
    monkeypatch.setattr(api, "get_messaging", lambda: messaging)
    monkeypatch.setattr(api, "get_db", lambda: None)
    monkeypatch.setattr(api, "get_servers", lambda: servers)
    return created


def test_spoofed_channel_and_server_are_not_persisted(monkeypatch):
    """The message's conversation and its server win over request-body values."""
    msg = SimpleNamespace(id=5, author_id=9, content="bad", conversation_id=777)
    servers = SimpleNamespace(
        get_channel_by_conversation=lambda cid: {"id": 777, "server_id": 888}
    )
    created = _patch(monkeypatch, msg, servers)

    asyncio.run(report_message(_body(999, 666), cast(Any, _User(2))))

    assert created["channel_id"] == 777
    assert created["server_id"] == 888


def test_unresolvable_context_is_not_taken_from_the_body(monkeypatch):
    """Neither half falls back to the caller's values when resolution fails.

    ``dc215e2cc`` gave both message-report paths the same fail-closed contract
    as the enhanced path: the conversation id is not persisted as a channel id
    either, because without the servers module the route cannot prove the id
    is not a private-channel alias. The report still names the message; only
    the moderation-queue context stays NULL.
    """
    msg = SimpleNamespace(id=5, author_id=9, content="bad", conversation_id=777)
    created = _patch(monkeypatch, msg, servers=None)

    asyncio.run(report_message(_body(999, 666), cast(Any, _User(2))))

    assert created["channel_id"] is None
    assert created["server_id"] is None


def test_malformed_context_row_fails_closed(monkeypatch):
    """A malformed servers row cannot be turned into a spoofable server id."""
    msg = SimpleNamespace(id=5, author_id=9, content="bad", conversation_id=777)
    servers = SimpleNamespace(
        get_channel_by_conversation=lambda cid: {"id": 777, "server_id": "not-an-id"}
    )
    created = _patch(monkeypatch, msg, servers)

    asyncio.run(report_message(_body(999, 666), cast(Any, _User(2))))

    assert created["channel_id"] == 777
    assert created["server_id"] is None
