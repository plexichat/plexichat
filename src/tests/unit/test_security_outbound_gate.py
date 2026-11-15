"""The single outbound URL gate that replaced three local SSRF wrappers."""

from unittest.mock import patch

import pytest

from src.core.security.errors import OutboundUrlRejected
from src.core.security.outbound import (
    OutboundUrl,
    parse_outbound_url,
    resolve_outbound_url,
)


def _validator_returning(hostname: str, address: str):
    patcher = patch("src.core.security.outbound.URLValidator")
    validator = patcher.start()
    validator.return_value.validate_url_for_request.return_value = (
        hostname,
        address,
    )
    return patcher, validator


def test_http_url_is_resolved_to_a_connect_address():
    patcher, _ = _validator_returning("example.com", "203.0.113.9")
    try:
        result = parse_outbound_url("  https://example.com/invite  ")
    finally:
        patcher.stop()

    assert isinstance(result, OutboundUrl)
    # Whitespace is stripped before storage, and the resolved address is what a
    # caller must connect to (connecting by name re-opens DNS rebinding).
    assert result.url == "https://example.com/invite"
    assert result.hostname == "example.com"
    assert result.address == "203.0.113.9"


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,<script>",
        "file:///etc/passwd",
        "ftp://example.com/x",
    ],
)
def test_non_http_schemes_are_rejected_without_resolving(url):
    with patch("src.core.security.outbound.URLValidator") as validator:
        with pytest.raises(OutboundUrlRejected) as rejection:
            parse_outbound_url(url, "attachment url")
    validator.return_value.validate_url_for_request.assert_not_called()
    assert "attachment url" in str(rejection.value)


def test_internal_target_is_rejected_and_the_address_is_not_echoed():
    with patch("src.core.security.outbound.URLValidator") as validator:
        validator.return_value.validate_url_for_request.side_effect = ValueError(
            "Access to 10.0.0.5 is forbidden"
        )
        with pytest.raises(OutboundUrlRejected) as rejection:
            parse_outbound_url("https://internal.example/x", "server icon URL")

    # The log keeps the reason; the exception the API layer sees does not.
    assert "10.0.0.5" not in str(rejection.value)
    assert "server icon URL" in str(rejection.value)


def test_optional_fields_pass_through_untouched():
    assert resolve_outbound_url(None) is None
    assert resolve_outbound_url("") == ""


def test_blank_url_is_rejected_when_a_url_is_expected():
    with pytest.raises(OutboundUrlRejected):
        parse_outbound_url("   ")


def test_rejection_is_a_value_error_for_existing_handlers():
    with patch("src.core.security.outbound.URLValidator"):
        with pytest.raises(ValueError):
            parse_outbound_url("file:///tmp/x")
