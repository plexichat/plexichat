"""Regression tests for feedback free-text injection hardening.

Feedback content is persisted verbatim and rendered in moderator UIs and log
lines. The request schema only bounds length, so a caller constructing the
model directly could previously smuggle C0 control bytes (log-line forging,
header/attribute break-outs) or Unicode bidi/invisible overrides (UI text
spoofing) into stored feedback. The route now rejects those code points
before any rate-limit check or core write.
"""

from typing import cast

import pytest
from fastapi import Request

from src.core.auth.models import TokenInfo
from src.api.routes import feedback as feedback_routes
from src.api.routes.feedback import _safe_feedback_content


def test_safe_feedback_content_allows_plain_multiline_text():
    text = "Please fix the thing\nSecond line\twith a tab"
    assert _safe_feedback_content(text) == text


@pytest.mark.parametrize("bad", ["\x00", "\x1b[31m", "\x08", "\x7f", "a\r\nb"])
def test_safe_feedback_content_rejects_control_bytes(bad):
    with pytest.raises(ValueError):
        _safe_feedback_content(bad)


@pytest.mark.parametrize("codepoint", [0x200B, 0x202E, 0x2066, 0xFEFF])
def test_safe_feedback_content_rejects_bidi_and_invisible(codepoint):
    with pytest.raises(ValueError):
        _safe_feedback_content("looks" + chr(codepoint) + "normal")


def test_safe_feedback_content_rejects_non_string():
    with pytest.raises(ValueError):
        _safe_feedback_content({"not": "text"})


def _model(content):
    from src.api.schemas.feedback import FeedbackCreate

    return FeedbackCreate(content=content, category=None, rating=None)


@pytest.mark.asyncio
async def test_submit_feedback_rejects_control_characters_before_core(monkeypatch):
    """The HTTP boundary maps disallowed characters to 400, never storing them."""
    from types import SimpleNamespace

    from fastapi import HTTPException

    called = {"core": False}

    def fake_submit(**kwargs):
        called["core"] = True
        return 1

    import src.core.feedback as core_feedback

    monkeypatch.setattr(core_feedback, "submit_feedback", fake_submit)

    user = SimpleNamespace(user_id=1)
    request = type("Request", (), {"state": type("State", (), {"is_internal": False})()})()

    with pytest.raises(HTTPException) as exc:
        await feedback_routes.submit_feedback(
            _model("bad\x00content"), cast(Request, request), cast(TokenInfo, user)
        )
    assert exc.value.status_code == 400
    assert called["core"] is False
