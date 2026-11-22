"""Regression: an unparsable Referer must not leak its URL/query into logs.

``AuthenticationMiddleware._origin_allowed`` parses the browser-set Referer
only far enough to extract a scheme+host CORS candidate. A malformed authority
(bad port, unbalanced IPv6 bracket) makes ``urlsplit`` raise ``ValueError``;
the old handler logged ``referer!r`` -- the *entire* header value. A Referer is
a full URL, so its path/query can carry live credentials (password-reset
``code``, OAuth ``code``, signed-URL ``sig``, session tokens).

The AppLogger sink is world-readable on disk and mirrored to the console, and
``SanitizingFilter`` only masks a fixed set of ``key=value`` names. Secrets in
``code=``/``sig=`` query parameters pass through it verbatim (CWE-532). The fix
keeps the diagnostic ("an unparsable Referer was ignored") but omits the value.

These tests drive the real ``_origin_allowed`` and feed its log call through a
real ``SanitizingFilter``-equipped logger into a sink, so they assert on what
actually reaches the log, not on a mocked string.
"""

from __future__ import annotations

import io
import logging
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.api.middleware.authentication import AuthenticationMiddleware
from src.utils.common_utils.utils.logger.core import SanitizingFilter

pytestmark = [pytest.mark.security, pytest.mark.unit]

# Unbalanced IPv6 bracket -> urlsplit raises ValueError. The query still carries
# a credential that must never be written to the log.
CANARY = "CANARY_RESET_CODE_9f8e7d1234"
REFERER = f"https://[::1/account/reset?code={CANARY}&state=opaque"


def _sink():
    """A logger wired like the app's file/console logger (real sanitizing filter)."""
    stream = io.StringIO()
    logger = logging.getLogger(f"referer.log.redaction.{uuid.uuid4().hex}")
    logger.handlers.clear()
    logger.filters.clear()
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))
    logger.addHandler(handler)
    logger.addFilter(SanitizingFilter())
    return logger, stream


def _request(referer: str):
    return SimpleNamespace(headers={"Referer": referer})


def _run(referer: str):
    sink, stream = _sink()
    # Route the middleware's debug call through the real sanitizing sink.
    with patch(
        "src.api.middleware.authentication.logger.debug", side_effect=sink.debug
    ):
        allowed = AuthenticationMiddleware._origin_allowed(_request(referer))
    return allowed, stream.getvalue()


def test_unparsable_referer_is_still_rejected():
    """The security decision is unchanged: an unparsable Referer yields no origin."""
    allowed, _ = _run(REFERER)
    assert allowed is False


def test_unparsable_referer_secret_never_reaches_log_sink():
    """A query-string credential in the Referer must not be persisted."""
    _, rendered = _run(REFERER)

    assert CANARY not in rendered, (
        f"password-reset code from the Referer reached the log sink: {rendered}"
    )
    # The whole URL (path + query) is sensitive, not just the canary value.
    assert "code=" not in rendered, f"query string logged verbatim: {rendered}"
    assert "account/reset" not in rendered, f"URL path logged verbatim: {rendered}"
    assert "?" not in rendered, f"full URL with query logged: {rendered}"


def test_legitimate_diagnostic_is_preserved():
    """Fail closed without dropping the audit signal for the ignored header."""
    _, rendered = _run(REFERER)

    assert "ignoring unparsable Referer origin" in rendered, (
        f"the diagnostic explaining the CSRF decision was lost: {rendered}"
    )
