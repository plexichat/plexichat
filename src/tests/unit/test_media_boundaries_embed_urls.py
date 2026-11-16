"""Embed URL fields must pass the scheme and SSRF gates before storage."""

import pytest

from src.core.embeds.exceptions import InvalidUrlError
from src.core.embeds.validator import (
    enforce_ssrf_gate,
    reject_non_http_url,
    validate_embed_url,
)


class TestRejectNonHttpUrl:
    @pytest.mark.parametrize(
        "value",
        [
            "javascript:alert(1)",
            "data:text/html,x",
            "vbscript:msgbox",
            "ftp://example.com/x",
            "file:///etc/passwd",
            "//example.com/x",
            "example.com/x",
            "",
            None,
        ],
    )
    def test_non_http_schemes_rejected(self, value):
        with pytest.raises(InvalidUrlError):
            reject_non_http_url(value)

    def test_credentials_rejected(self):
        with pytest.raises(InvalidUrlError):
            reject_non_http_url("https://user:pass@example.com/x")

    def test_http_and_https_accepted(self):
        assert reject_non_http_url("https://example.com/x") == "https://example.com/x"
        assert reject_non_http_url("http://example.com/x") == "http://example.com/x"


class TestValidateEmbedUrl:
    @pytest.mark.parametrize(
        "value",
        [
            "http://127.0.0.1/x",
            "http://localhost/x",
            "http://[::1]/x",
            "http://10.0.0.1/x",
        ],
    )
    def test_ssrf_targets_rejected(self, value):
        with pytest.raises(InvalidUrlError):
            validate_embed_url(value)

    def test_oversize_rejected(self):
        with pytest.raises(InvalidUrlError):
            validate_embed_url("https://example.com/" + "a" * 5000)

    def test_relative_media_path_allowed_when_requested(self):
        # Shape-valid media paths are the one exception to the scheme gate.
        result = validate_embed_url("/api/v1/media/abc.png", allow_relative=True)
        assert result.startswith("/api/v1/media/")

    def test_relative_path_not_allowed_by_default(self):
        with pytest.raises(InvalidUrlError):
            validate_embed_url("/api/v1/media/abc.png")


class TestEnforceSsrfGate:
    def test_public_host_passes(self):
        assert enforce_ssrf_gate("https://example.com/x") == "https://example.com/x"

    def test_gate_failure_raises_invalid_url(self, monkeypatch):
        import src.utils.security as sec

        class _Boom:
            def validate_url_for_request(self, url):
                raise ValueError("blocked")

        monkeypatch.setattr(sec, "URLValidator", _Boom)
        with pytest.raises(InvalidUrlError, match="SSRF"):
            enforce_ssrf_gate("https://example.com/x")
