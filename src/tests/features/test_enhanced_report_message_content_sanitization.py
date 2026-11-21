"""Enhanced reports must not persist raw control/bidi characters from the
reported message into the moderation queue.

The plain report route bounds and rejects control characters in the
``message_content`` preview it stores. The enhanced route used to slice the raw
message text (``msg.content[:500]``) instead, so a reporter could store ESC/NUL
bytes, a NEL or Unicode line separator, or a U+202E bidi override in a row that
is later rendered to moderators and written to log lines. Strip the shared
control-character set and bound the preview before persisting.
"""

import asyncio
from typing import Any, cast

from src.api.routes.features.reports import (
    EnhancedReportRequest,
    _safe_message_content,
    submit_enhanced_report,
)


class _FakeUser:
    def __init__(self, user_id: int):
        self.user_id = user_id


class _FakeMessage:
    def __init__(self, content: str):
        self.author_id = 42
        self.conversation_id = 11
        self.content = content


class _FakeMessaging:
    def __init__(self, message):
        self._message = message

    def get_message(self, user_id, message_id):
        return self._message


class _FakeDB:
    def __init__(self):
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))
        return None

    def fetch_one(self, query, params=None):
        return None


def _patch_common(monkeypatch, db, messaging):
    import src.api as api

    monkeypatch.setattr(api, "get_db", lambda: db)
    monkeypatch.setattr(api, "get_messaging", lambda: messaging)
    monkeypatch.setattr(api, "get_servers", lambda: None)
    monkeypatch.setattr(
        "src.core.database.check_rate_limit", lambda *args, **kwargs: (True, 9)
    )


def _stored_message_content(db):
    inserts = [entry for entry in db.executed if "INSERT INTO reports" in entry[0]]
    assert len(inserts) == 1
    # INSERT column order: id, reporter, type, target, channel, server, reason,
    # category, details, evidence_ids, message_content, reported_user_id, ...
    return inserts[0][1][10]


def test_safe_message_content_strips_controls_and_bounds():
    raw = "hello\x1b[2J\x00world\u2028line\u2029\u202ehttp://evil.example\x7f"
    cleaned = _safe_message_content(raw)
    assert cleaned is not None
    assert "\x1b" not in cleaned
    assert "\x00" not in cleaned
    assert "\u2028" not in cleaned
    assert "\u2029" not in cleaned
    assert "\u202e" not in cleaned
    assert "\x7f" not in cleaned
    # ESC is removed; the literal "[2J" that followed it is ordinary text and
    # stays -- it is the invisible control byte that enabled the terminal escape.
    assert "hello[2Jworldlinehttp://evil.example" == cleaned

    assert _safe_message_content("x" * 1000) == "x" * 500
    assert _safe_message_content("") is None
    assert _safe_message_content(None) is None
    assert _safe_message_content(123) is None


def test_route_stores_sanitized_message_content(monkeypatch):
    raw = "looks safe \u202e" + "evil.example" + "\x00\x1b"
    db = _FakeDB()
    messaging = _FakeMessaging(_FakeMessage(raw))
    _patch_common(monkeypatch, db, messaging)

    req = EnhancedReportRequest(
        target_type="message",
        target_id="100",
        reason="spam",
    )
    result = asyncio.run(submit_enhanced_report(req, cast(Any, _FakeUser(1))))

    assert result["success"] is True
    stored = _stored_message_content(db)
    assert stored is not None
    assert "\u202e" not in stored
    assert "\x00" not in stored
    assert "\x1b" not in stored


def test_route_still_stores_ordinary_message_text(monkeypatch):
    db = _FakeDB()
    messaging = _FakeMessaging(_FakeMessage("this is a normal reported message"))
    _patch_common(monkeypatch, db, messaging)

    req = EnhancedReportRequest(target_type="message", target_id="100", reason="spam")
    asyncio.run(submit_enhanced_report(req, cast(Any, _FakeUser(1))))

    assert _stored_message_content(db) == "this is a normal reported message"
