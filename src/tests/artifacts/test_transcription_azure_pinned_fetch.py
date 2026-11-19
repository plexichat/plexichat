"""Regression tests for Azure transcription URL validation and fetch pinning.

Two failures are covered:

* ``_validate_azure_content_url`` used to accept credential-bearing, CRLF and
  unbounded URLs. They reached the provider (and the logs) unfiltered.
* The provider-supplied ``files``/``contentUrl`` was validated (DNS + private-IP
  check) but then fetched by *name* with ``requests.get``. That re-resolves the
  host a second time, so a rebinding response can pass validation with a public
  IP and connect to loopback/link-local on the actual fetch.
"""

from unittest.mock import patch

import pytest

from src.core.artifacts.transcription.provider import (
    MAX_AZURE_CONTENT_URL_LENGTH,
    _pinned_azure_get,
    _validate_azure_content_url,
)
from src.utils.security import URLValidator

PUBLIC_URL = "https://recording.example.test/audio.wav?sig=abc"
PUBLIC_HOST = "recording.example.test"
APPROVED_IP = "93.184.216.34"


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://recording.example.test/audio.wav",
        "ftp://recording.example.test/audio.wav",
        "https://user:pass@recording.example.test/audio.wav",
        "https://recording.example.test:999999/audio.wav",
        "https://recording.example.test:notaport/audio.wav",
        "https://recording.example.test/\r\nInjected: header",
        "https://" + "a" * (MAX_AZURE_CONTENT_URL_LENGTH + 1) + ".test/x",
    ],
)
def test_validate_azure_content_url_rejects_unsafe_shapes(bad_url):
    with pytest.raises(ValueError):
        _validate_azure_content_url(bad_url)


def test_validate_azure_content_url_accepts_public_https():
    with patch.object(
        URLValidator,
        "validate_url_for_request",
        return_value=(PUBLIC_HOST, APPROVED_IP),
    ):
        assert _validate_azure_content_url(PUBLIC_URL) == PUBLIC_URL


def test_pinned_get_connects_to_approved_ip_with_host_and_sni():
    """The fetch must use the IP the gate approved, never the hostname."""
    sentinel = object()
    with (
        patch.object(
            URLValidator,
            "validate_url_for_request",
            return_value=(PUBLIC_HOST, APPROVED_IP),
        ),
        patch("httpx.get", return_value=sentinel) as mocked,
    ):
        result = _pinned_azure_get(
            PUBLIC_URL, headers={"Ocp-Apim-Subscription-Key": "k"}, timeout=7
        )

    assert result is sentinel
    args, kwargs = mocked.call_args
    request_url = args[0]
    assert APPROVED_IP in request_url
    assert PUBLIC_HOST not in request_url
    assert kwargs["headers"]["Host"] == PUBLIC_HOST
    assert kwargs["headers"]["Ocp-Apim-Subscription-Key"] == "k"
    assert kwargs["extensions"] == {"sni_hostname": PUBLIC_HOST}
    assert kwargs["timeout"] == 7


def test_pinned_get_refuses_when_dns_check_fails():
    """No HTTP request may be made if the SSRF gate rejects the target."""
    with (
        patch.object(
            URLValidator,
            "validate_url_for_request",
            side_effect=ValueError("resolves to a forbidden address"),
        ),
        patch("httpx.get") as mocked,
    ):
        with pytest.raises(ValueError):
            _pinned_azure_get(PUBLIC_URL)
    mocked.assert_not_called()
