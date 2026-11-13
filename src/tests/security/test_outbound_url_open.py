"""Tests for :mod:`src.utils.outbound`.

Every outbound request in the backend goes through this helper, so the scheme
allowlist is the transport-level floor for SSRF-style redirection: a URL that
configuration controls must not be able to reach ``file:``, ``data:`` or any
other scheme ``urllib`` knows about.
"""

from unittest.mock import MagicMock
from urllib.request import Request

import pytest

from src.utils import outbound


@pytest.fixture
def opener(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace the module-level opener so no real socket is opened."""
    open_mock = MagicMock(return_value="response")
    monkeypatch.setattr(outbound._OPENER, "open", open_mock)
    return open_mock


def test_http_request_reaches_the_opener(opener: MagicMock) -> None:
    request = Request("http://example.com/hook")

    assert outbound.urlopen(request, timeout=5) == "response"
    opener.assert_called_once_with(request, timeout=5)


def test_https_request_reaches_the_opener(opener: MagicMock) -> None:
    request = Request("https://example.com/hook")

    assert outbound.urlopen(request) == "response"
    opener.assert_called_once_with(request, timeout=30)


def test_default_timeout_is_applied(opener: MagicMock) -> None:
    outbound.urlopen("https://example.com/hook")

    assert opener.call_args.kwargs["timeout"] == 30


def test_plain_string_urls_are_accepted(opener: MagicMock) -> None:
    outbound.urlopen("https://example.com/hook?x=1", timeout=2.5)

    opener.assert_called_once_with("https://example.com/hook?x=1", timeout=2.5)


@pytest.mark.parametrize("url", ["HTTP://Example.com/upper"])
def test_scheme_match_is_case_insensitive(opener: MagicMock, url: str) -> None:
    outbound.urlopen(url)

    opener.assert_called_once()


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/secret",
        "gopher://example.com/",
        "data:text/plain,hello",
        "javascript:alert(1)",
        "jar:http://example.com/x.jar!/",
        "http:///no-host",
        "https://",
        "//example.com/protocol-relative",
        "example.com/no-scheme",
        "/relative/path",
        "",
    ],
)
def test_non_http_urls_never_reach_the_opener(opener: MagicMock, url: str) -> None:
    with pytest.raises(ValueError, match="refusing to open non-http"):
        outbound.urlopen(url)

    opener.assert_not_called()


def test_refusal_names_the_rejected_url(opener: MagicMock) -> None:
    with pytest.raises(ValueError) as excinfo:
        outbound.urlopen(Request("file:///etc/passwd"))

    assert "file:///etc/passwd" in str(excinfo.value)
    opener.assert_not_called()
