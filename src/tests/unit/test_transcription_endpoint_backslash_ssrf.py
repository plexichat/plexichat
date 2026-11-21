"""Regression: a backslash in an endpoint's authority is a parser differential.

``_require_public_https_endpoint`` used to shape-check ``openai_api_base`` with
``require_https_url`` and then decide whether the host was an IP literal via
``ipaddress.ip_address``. A trailing backslash makes ``urlparse`` report a host
like ``169.254.169.254\\`` -- truthy, so the shape check passed, but ``ipaddress``
could not parse it and a numeric-literal regex did not match it either, so the
host was treated as a harmless DNS name and the endpoint was accepted. A
WHATWG-following HTTP client reads that backslash as ``/`` and connects to the
link-local metadata address, leaking the OpenAI Bearer token.

The shared SSRF gates (``src.utils.security.URLValidator`` and
``src.core.security.outbound``) already reject a backslash in the authority;
the provider endpoint gate must agree with them.
"""

import pytest

from src.core.artifacts.transcription.provider import (
    OpenAIWhisperProvider,
    _require_public_https_endpoint,
)
from src.core.security.hardening import HardeningViolation


@pytest.mark.parametrize(
    "url",
    [
        # Trailing backslash on a link-local metadata literal: the exact
        # pre-fix bypass. urlparse sees host "169.254.169.254\\".
        "https://169.254.169.254\\/v1",
        "https://169.254.169.254\\/latest/meta-data",
        # Loopback and private literals with the same trick.
        "https://127.0.0.1\\/v1",
        "https://10.0.0.5\\/internal",
    ],
)
def test_endpoint_rejects_backslash_in_authority(url):
    with pytest.raises(HardeningViolation, match="backslash"):
        _require_public_https_endpoint(url, "openai_api_base")
    with pytest.raises(HardeningViolation, match="backslash"):
        OpenAIWhisperProvider({"openai_api_key": "k", "openai_api_base": url})


@pytest.mark.parametrize(
    "url",
    [
        # A backslash before the '@' is caught by the credential rule first.
        "https://169.254.169.254\\@public.example/v1",
    ],
)
def test_endpoint_rejects_backslash_credential_smuggling(url):
    with pytest.raises(HardeningViolation):
        OpenAIWhisperProvider({"openai_api_key": "k", "openai_api_base": url})


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1/audio/transcriptions",
        # A backslash in the *path* is not an authority confusion; urlparse's
        # netloc stops at the first real '/' so this stays a normal host.
        "https://api.openai.com/v1/audio\\transcriptions",
        "https://openai.example.test/v1?q=a\\b",
    ],
)
def test_endpoint_still_accepts_public_urls(url):
    assert _require_public_https_endpoint(url, "openai_api_base") == url
    assert (
        OpenAIWhisperProvider({"openai_api_key": "k", "openai_api_base": url})._api_url
        == url
    )
