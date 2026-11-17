"""Regression: URL fields reject credentials, bad schemes, and unsafe lengths.

Every URL carried in a request must be a well-formed absolute http(s) URL with
no embedded credentials. A field with a host fixed by the API contract can add
an allowlist on top. The SSRF gate is a separate concern and is not weakened.
"""

import pytest
from pydantic import BaseModel, Field, ValidationError

from src.api.schemas.url_types import (
    HttpUrl,
    allowlisted_http_url,
    validate_http_url,
)
from src.api.schemas.webhooks import WebhookCreateRequest


class _UrlCarrier(BaseModel):
    url: HttpUrl = Field(..., min_length=1)


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,x",
        "file:///etc/passwd",
        "ftp://example.com/x",
        "/relative/path",
        "example.com/path",
        "http://",
        "http://user:pass@example.com/x",  # embedded credentials
        "https://user@example.com/x",
        "https://example.com/\x00x",  # control character
        "https://example.com/\u202ex",  # bidi override
        "https://example.com/" + "a" * 3000,  # over MAX_URL_LENGTH
    ],
)
def test_url_policy_rejects_unsafe_urls(url):
    with pytest.raises(ValueError):
        validate_http_url(url, "url")


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/x",
        "http://localhost:8000/cb",
        "https://cdn.example.com/a/b.png?size=256#frag",
    ],
)
def test_url_policy_accepts_safe_urls(url):
    assert validate_http_url(url, "url") == url


def test_annotation_enforces_the_policy():
    with pytest.raises(ValidationError):
        _UrlCarrier(url="https://user:pass@example.com/")
    assert _UrlCarrier(url="https://example.com/x").url == "https://example.com/x"


def test_host_allowlist():
    OnlyExample = allowlisted_http_url("example.com", "CDN.Example.com")

    class Carrier(BaseModel):
        url: OnlyExample = Field(..., min_length=1)  # pyright: ignore[reportInvalidTypeForm]

    assert Carrier(url="https://example.com/x").url == "https://example.com/x"
    assert Carrier(url="https://cdn.example.com/y").url == "https://cdn.example.com/y"
    with pytest.raises(ValidationError):
        Carrier(url="https://evil.test/x")
    with pytest.raises(ValidationError):
        Carrier(url="https://example.com.evil.test/x")


def test_request_model_url_field_is_hardened():
    with pytest.raises(ValidationError):
        WebhookCreateRequest(  # pyright: ignore[reportCallIssue]
            channel_id="123456789012345678",
            name="hooks",
            avatar_url="https://user:pass@evil.test/a.png",
        )
