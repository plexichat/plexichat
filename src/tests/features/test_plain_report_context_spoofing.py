"""Regression: the plain message-report route must not persist forged context.

``get_message`` proves the reporter can *see* a message, but says nothing about
which channel/server a report is attributed to. The enhanced-report path
already derives both from the server-side conversation and fails closed; the
plain ``report_message`` route used to fall back to the caller-supplied
``channel_id``/``server_id`` when the message had no ``conversation_id`` (e.g. a
DM), letting a reporter plant a report in a moderation queue they are not even
a member of. These tests pin the fail-closed contract at the plain route.
"""

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from src.api.routes.reports import report_message
from src.api.schemas.reports import MessageReportRequest


class _FakeUser:
    def __init__(self, user_id: int):
        self.user_id = user_id


class _FakeMessage:
    def __init__(self, author_id: int, conversation_id, content: str = "bad content"):
        self.author_id = author_id
        self.conversation_id = conversation_id
        self.content = content


class _FakeMessaging:
    def __init__(self, message):
        self._message = message

    def get_message(self, user_id, message_id):
        return self._message


class _FakeServers:
    def __init__(self, mapping):
        self._mapping = mapping
        self.looked_up = []

    def get_channel_by_conversation(self, conversation_id):
        self.looked_up.append(conversation_id)
        return self._mapping.get(conversation_id)


class _FakeReports:
    def __init__(self):
        self.calls = []

    def find_open_message_report(self, reporter_id, message_id):
        return None

    def report_message(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(id=123)


def _request():
    # Forged context: the reporter claims an unrelated channel and server.
    return MessageReportRequest(
        message_id="100",
        channel_id="999999",
        server_id="999999",
        reason="spam",
        category="spam",
    )


def _patch(monkeypatch, reports, messaging, servers):
    import src.api as api

    monkeypatch.setattr(api, "get_reports", lambda: reports)
    monkeypatch.setattr(api, "get_messaging", lambda: messaging)
    monkeypatch.setattr(api, "get_servers", lambda: servers)
    monkeypatch.setattr("src.core.database.check_rate_limit", lambda *a, **k: (True, 9))


def _submit(monkeypatch, conversation_id, servers):
    reports = _FakeReports()
    messaging = _FakeMessaging(
        _FakeMessage(author_id=42, conversation_id=conversation_id)
    )
    _patch(monkeypatch, reports, messaging, servers)
    result = asyncio.run(report_message(_request(), cast(Any, _FakeUser(1))))
    assert result.success is True
    assert len(reports.calls) == 1
    return reports.calls[0]


@pytest.mark.unit
@pytest.mark.security
def test_forged_context_ignored_when_message_has_no_conversation(monkeypatch):
    """A DM (no conversation) must store NULL context, not the forged body."""
    call = _submit(monkeypatch, conversation_id=None, servers=_FakeServers({}))
    assert call["channel_id"] is None
    assert call["server_id"] is None
    assert 999999 not in (call["channel_id"], call["server_id"])


@pytest.mark.unit
@pytest.mark.security
def test_context_comes_from_server_side_conversation(monkeypatch):
    """A resolvable conversation stores the real channel/server, not the body."""
    servers = _FakeServers({11: {"id": "555", "server_id": "777"}})
    call = _submit(monkeypatch, conversation_id=11, servers=servers)
    assert servers.looked_up == [11]
    assert call["channel_id"] == 555
    assert call["server_id"] == 777
    assert 999999 not in (call["channel_id"], call["server_id"])


@pytest.mark.unit
@pytest.mark.security
def test_fails_closed_when_servers_module_unavailable(monkeypatch):
    call = _submit(monkeypatch, conversation_id=11, servers=None)
    assert call["channel_id"] is None
    assert call["server_id"] is None


@pytest.mark.unit
@pytest.mark.security
def test_fails_closed_on_malformed_conversation_row(monkeypatch):
    servers = _FakeServers({11: {"id": "not-an-id", "server_id": "-4"}})
    call = _submit(monkeypatch, conversation_id=11, servers=servers)
    assert call["channel_id"] is None
    assert call["server_id"] is None
