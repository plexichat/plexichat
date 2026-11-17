"""Regression tests: enhanced-report context must be server-derived.

A reporter may prove they can view a message (``get_message`` performs the
participant check), but the channel/server a report is attributed to must come
from the server-side conversation — never from the request body. Previously a
caller could send arbitrary ``channel_id``/``server_id`` and have them stored
verbatim in the ``reports`` row, planting a report in an unrelated moderation
context (queue poisoning / context spoofing).
"""

import asyncio
from typing import Any, cast

import pytest

from src.api.routes.features.reports import (
    EnhancedReportRequest,
    _message_report_context,
    submit_enhanced_report,
)


class _FakeUser:
    def __init__(self, user_id: int):
        self.user_id = user_id


class _FakeMessage:
    def __init__(self, author_id: int, conversation_id: int):
        self.author_id = author_id
        self.conversation_id = conversation_id
        self.content = "reported content"


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


class _FakeDB:
    def __init__(self):
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))
        return None

    def fetch_one(self, query, params=None):
        # No duplicate report, no admin gate reads on the submit path.
        return None


def _patch_common(monkeypatch, db, messaging, servers):
    import src.api as api

    monkeypatch.setattr(api, "get_db", lambda: db)
    monkeypatch.setattr(api, "get_messaging", lambda: messaging)
    monkeypatch.setattr(api, "get_servers", lambda: servers)
    monkeypatch.setattr(
        "src.core.database.check_rate_limit", lambda *args, **kwargs: (True, 9)
    )


class TestMessageReportContextHelper:
    def test_uses_get_channel_by_conversation(self, monkeypatch):
        import src.api as api

        servers = _FakeServers({11: {"id": "555", "server_id": "777"}})
        monkeypatch.setattr(api, "get_servers", lambda: servers)
        assert _message_report_context(11) == (555, 777)
        assert servers.looked_up == [11]

    def test_fails_closed_on_dm_and_bad_rows(self, monkeypatch):
        import src.api as api

        servers = _FakeServers(
            {
                12: {"id": "555"},  # no server_id -> NULL server
                13: {"id": "-4", "server_id": "0"},  # invalid ids -> NULL
                14: "not-a-dict",  # malformed row -> no context
            }
        )
        monkeypatch.setattr(api, "get_servers", lambda: servers)
        assert _message_report_context(None) == (None, None)
        assert _message_report_context(12) == (555, None)
        assert _message_report_context(13) == (None, None)
        assert _message_report_context(14) == (None, None)

    def test_fails_closed_when_lookup_raises(self, monkeypatch):
        import src.api as api

        class _Broken:
            def get_channel_by_conversation(self, conversation_id):
                raise RuntimeError("db down")

        monkeypatch.setattr(api, "get_servers", lambda: _Broken())
        assert _message_report_context(11) == (None, None)


class TestEnhancedReportSubmission:
    def _report(self, **kwargs):
        base = {
            "target_type": "message",
            "target_id": "100",
            "reason": "spam",
            # Forged context: attacker claims a channel/server unrelated to
            # the message's real conversation.
            "channel_id": "999999",
            "server_id": "999999",
        }
        base.update(kwargs)
        return EnhancedReportRequest(**base)

    def _insert_params(self, db):
        inserts = [entry for entry in db.executed if "INSERT INTO reports" in entry[0]]
        assert len(inserts) == 1
        return inserts[0][1]

    def test_forged_channel_server_not_persisted(self, monkeypatch):
        db = _FakeDB()
        messaging = _FakeMessaging(_FakeMessage(author_id=42, conversation_id=11))
        servers = _FakeServers({11: {"id": "555", "server_id": "777"}})
        _patch_common(monkeypatch, db, messaging, servers)

        result = asyncio.run(
            submit_enhanced_report(self._report(), cast(Any, _FakeUser(1)))
        )

        assert result["success"] is True
        params = self._insert_params(db)
        # INSERT column order: id, reporter, type, target, channel, server, ...
        assert params[4] == 555  # real channel from server-side conversation
        assert params[5] == 777  # real server from server-side conversation
        assert 999999 not in params  # forged values never stored

    def test_unsafe_evidence_url_error_does_not_reflect_input(self, monkeypatch):
        db = _FakeDB()
        _patch_common(monkeypatch, db, None, None)

        req = EnhancedReportRequest(
            target_type="user",
            target_id="999",
            reason="spam",
            evidence_urls=["http://127.0.0.1/<img src=x onerror=alert(1)>"],
        )
        with pytest.raises(Exception) as exc:
            asyncio.run(submit_enhanced_report(req, cast(Any, _FakeUser(1))))
        error = cast(Any, exc.value)
        assert error.status_code == 400
        # The rejected (attacker-controlled) URL must never be echoed back.
        assert "127.0.0.1" not in str(error.detail)
        assert "onerror" not in str(error.detail)

    def test_malformed_context_still_rejected(self, monkeypatch):
        from fastapi import HTTPException

        db = _FakeDB()
        messaging = _FakeMessaging(_FakeMessage(author_id=42, conversation_id=11))
        servers = _FakeServers({11: {"id": "555", "server_id": "777"}})
        _patch_common(monkeypatch, db, messaging, servers)

        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                submit_enhanced_report(
                    self._report(server_id="not-an-id"), cast(Any, _FakeUser(1))
                )
            )
        assert exc.value.status_code == 400
        assert db.executed == []

    def test_forged_context_ignored_when_conversation_has_no_channel(self, monkeypatch):
        db = _FakeDB()
        messaging = _FakeMessaging(_FakeMessage(author_id=42, conversation_id=11))
        servers = _FakeServers({})  # DM / no channel mapping
        _patch_common(monkeypatch, db, messaging, servers)

        asyncio.run(submit_enhanced_report(self._report(), cast(Any, _FakeUser(1))))

        params = self._insert_params(db)
        assert params[4] is None
        assert params[5] is None
        assert 999999 not in params
