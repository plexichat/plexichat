"""Regression: secret query parameters must not reach the sanitized log sink.

``SanitizingFilter`` renders every log record and passes it through
``sanitize_log_message``. That function only masked a fixed set of key names and
only when one of its keyword triggers appeared elsewhere in the line, so a URL
logged by a redirect/proxy/media/OAuth path with ``?code=``, ``&sig=`` or
``&state=`` wrote the credential verbatim to the world-readable log files and
the console sink (CWE-532).

The fix redacts the values of sensitive keys in *query-like* ``key=value``
pairs (after ``?``/``&``, or the first pair of an ``&``-separated run), by key
rather than by trigger word. These tests drive the real filter into a sink and
assert both that every sensitive value is gone and that ordinary diagnostics,
non-sensitive query values and control-character scrubbing are unchanged.
"""

from __future__ import annotations

import io
import logging
import uuid

import pytest

from src.utils.common_utils.utils.logger.core import SanitizingFilter

pytestmark = [pytest.mark.security, pytest.mark.unit]

# No sanitizer trigger word ("token", "secret", "key", "auth", ...) so the test
# cannot pass via the pre-existing keyword pass -- it must exercise the new
# query-context redaction.
CANARY = "CANARY7744119988QQ"

SENSITIVE_KEYS = (
    "code",
    "sig",
    "signature",
    "state",
    "token",
    "access_token",
    "refresh_token",
    "password",
    "secret",
    "api_key",
    "key",
    "auth",
)


def _sink():
    """A logger wired like the app's file/console logger (real sanitizing filter)."""
    stream = io.StringIO()
    logger = logging.getLogger(f"query.log.redaction.{uuid.uuid4().hex}")
    logger.handlers.clear()
    logger.filters.clear()
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s - %(message)s"))
    logger.addHandler(handler)
    logger.addFilter(SanitizingFilter())
    return logger, stream


def _emit(msg: str) -> str:
    logger, stream = _sink()
    logger.debug(msg)
    return stream.getvalue()


# Keys the pre-existing keyword pass already covers trigger on their name; the
# four below ("code", "sig", "signature", "state") are the newly covered gap.
_NEWLY_COVERED_KEYS = ("code", "sig", "signature", "state")


@pytest.mark.parametrize("key", SENSITIVE_KEYS)
def test_sensitive_query_param_redacted_in_sink(key):
    """Each sensitive key's value is absent from the real log sink."""
    rendered = _emit(f"GET /api/v1/resource?{key}={CANARY}&page=2 HTTP/1.1")
    assert CANARY not in rendered, f"{key} value leaked into the sink: {rendered}"
    assert "********" in rendered, f"expected a redaction marker: {rendered}"
    if key in _NEWLY_COVERED_KEYS:
        # The new query pass is pair-scoped: the neighbouring benign pair and
        # the trailing HTTP token must survive unchanged.
        assert "page=2" in rendered, f"over-redaction dropped a benign pair: {rendered}"
        assert "HTTP/1.1" in rendered, f"over-redaction ate the suffix: {rendered}"


def test_bare_query_string_and_multiple_secrets_redacted():
    """A decoded query string without '?' and a run of secrets are all masked."""
    rendered = _emit(f"upstream query: code={CANARY}&sig={CANARY}&state={CANARY}")
    assert CANARY not in rendered, rendered
    assert "********" in rendered


def test_redirect_and_proxy_url_query_secrets_redacted():
    """Concrete leak sites: redirect URLs and failed proxy fetch URLs."""
    rendered = _emit(
        f"Following redirect to: https://cdn.example.com/cb?code={CANARY}#frag"
    )
    assert CANARY not in rendered, rendered
    rendered = _emit(
        f"Proxy fetch failed for https://x.test/p?access_token={CANARY}: boom"
    )
    assert CANARY not in rendered, rendered


def test_quoted_query_value_redacted():
    rendered = _emit(f'?code="{CANARY} with spaces"&x=1')
    assert CANARY not in rendered, rendered
    assert "with spaces" not in rendered, f"quoted value tail leaked: {rendered}"


def test_nonsensitive_query_values_preserved():
    """No over-redaction of benign query parameters."""
    rendered = _emit("GET /api?page=2&debug=true&sort=asc - 200")
    for value in ("page=2", "debug=true", "sort=asc"):
        assert value in rendered, f"over-redaction dropped {value}: {rendered}"


def test_normal_key_value_diagnostics_preserved():
    """A bare ``key=value`` outside a query context is left alone."""
    rendered = _emit("AdminID=5 Action=login Status=success")
    assert "AdminID=5 Action=login Status=success" in rendered
    rendered = _emit("error code=500 on /api")
    assert "code=500" in rendered, f"non-query 'code=' over-redacted: {rendered}"


def test_control_chars_still_scrubbed_with_query_redaction():
    """Adding query redaction must not weaken CWE-117 control-char scrubbing."""
    rendered = _emit(f"?code={CANARY}\r\nFORGED\x1b[2J&x=1")
    assert CANARY not in rendered, rendered
    assert "\r" not in rendered and "\x1b" not in rendered, rendered
    # Only the handler's single trailing newline remains -- the injected
    # CR/LF pair was escaped by the final scrubbing pass.
    assert rendered.count("\n") == 1, rendered
    assert "\\x0d\\x0a" in rendered


def test_nested_query_secret_redacted():
    """A secret inside a nested redirect/OAuth URL must not reach the sink.

    Without stopping values at ``?``, the outer ``redirect`` value swallows the
    whole nested URL, so the inner ``code=`` is never examined and leaks.
    """
    rendered = _emit(f"?redirect=https://cb.example.test/cb?code={CANARY}&page=2")
    assert CANARY not in rendered, rendered
    # The benign outer prefix and the neighbouring pair must survive.
    assert "redirect=https://cb.example.test/cb?" in rendered, rendered
    assert "page=2" in rendered, rendered

    rendered = _emit(f"&next=/local?state={CANARY}#frag")
    assert CANARY not in rendered, rendered


def test_nested_query_benign_pairs_preserved():
    """Stopping values at ``?`` must not over-redact a nested benign query."""
    rendered = _emit("GET /?redirect=https://x.test/p?page=2&debug=true - 200")
    for value in ("page=2", "debug=true"):
        assert value in rendered, f"over-redaction dropped {value}: {rendered}"


def test_whitespace_around_equals_redacted():
    """Whitespace around ``=`` is tolerated, as in the keyword pass."""
    for msg in (
        f"?code = {CANARY}&page=2",
        f"?code= {CANARY}&page=2",
        f"?code ={CANARY}&page=2",
    ):
        rendered = _emit(msg)
        assert CANARY not in rendered, rendered
        assert "page=2" in rendered, f"over-redaction dropped benign pair: {rendered}"
