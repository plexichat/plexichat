"""Regression tests for the artifact export security boundary."""

from __future__ import annotations

import pytest

from src.core.artifacts.export import (
    ExportTooLarge,
    UnsafeExportURL,
    bounded_chunks,
    bound_export_text,
    content_disposition_header,
    enforce_export_limit,
    safe_export_filename,
    validate_export_url,
)

TRAVERSAL_TITLES = [
    "../../etc/passwd",
    "..\\..\\windows\\win.ini",
    "/absolute/path/secret",
    "C:\\Windows\\System32\\drivers\\etc\\hosts",
    "....//....//etc/passwd",
    "\u202eexe.doc",
    'evil"\r\nX-Injected: 1',
    "\x00../secret",
]


@pytest.mark.parametrize("title", TRAVERSAL_TITLES)
def test_export_filename_never_contains_a_path_or_header_control(title):
    ascii_name, utf8_name = safe_export_filename(title, "html")
    assert "/" not in ascii_name
    assert "\\" not in ascii_name
    assert ".." not in ascii_name
    assert '"' not in ascii_name
    assert "\r" not in ascii_name and "\n" not in ascii_name
    # RFC 5987 percent-encoding must also hide separators and quotes.
    assert "/" not in utf8_name and "\\" not in utf8_name
    assert ".." not in utf8_name
    assert ascii_name.endswith(".html")


def test_content_disposition_header_has_no_injected_fields():
    header = content_disposition_header('report"\r\nX-Evil: 1\nX-Other: 2', "pdf")
    # No CR/LF means no second header field can be started. No quote means the
    # quoted filename= value cannot be terminated early. The sanitised text may
    # survive as inert characters, but it can never become a separate field.
    assert "\r" not in header and "\n" not in header
    # Only the opening and closing quote of filename=... survive.
    assert header.count('"') == 2
    assert "; X-" not in header
    assert header.count("filename=") == 1  # ascii form
    assert header.count("filename*=") == 1  # RFC 5987 form
    assert header.startswith('attachment; filename="')
    assert "; filename*=UTF-8''" in header


def test_non_ascii_title_keeps_a_usable_ascii_fallback():
    ascii_name, utf8_name = safe_export_filename("季度报告", "md")
    assert ascii_name == "export.md"
    assert utf8_name  # percent-encoded UTF-8, never a raw non-ASCII byte
    assert all(ord(ch) < 128 for ch in utf8_name)


def test_unsupported_format_is_rejected():
    with pytest.raises(ValueError):
        safe_export_filename("doc", "exe")


def test_export_size_limits_fail_closed():
    with pytest.raises(ExportTooLarge):
        enforce_export_limit(b"a" * 129, max_bytes=128)
    with pytest.raises(ExportTooLarge):
        bound_export_text("x" * 129, max_chars=128)
    assert enforce_export_limit(b"ok", max_bytes=128) == b"ok"


def test_bounded_chunks_raise_before_emitting_any_chunk():
    gen = bounded_chunks(b"a" * 5000, chunk_size=100, max_bytes=128)
    with pytest.raises(ExportTooLarge):
        next(gen)
    assert list(bounded_chunks(b"a" * 200, chunk_size=64, max_bytes=256))


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/latest/meta-data/",
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
        "http://[::1]/admin",
        "http://10.0.0.5/internal",
        "http://192.168.1.1/",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http://user:pass@example.com/export",
        "http://[::ffff:127.0.0.1]/",
        "https://example.com/\r\nX-Evil: 1",
    ],
)
def test_ssrf_export_urls_are_rejected(url):
    with pytest.raises(UnsafeExportURL):
        validate_export_url(url)


def test_public_export_url_is_allowed():
    assert validate_export_url("https://example.com/report.json") == (
        "https://example.com/report.json"
    )
    assert validate_export_url("https://93.184.216.34/report.json")


def test_host_allowlist_blocks_unexpected_destination(monkeypatch):
    import socket

    def fake_getaddrinfo(host, port, **kwargs):
        assert host == "cdn.example.com"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert validate_export_url(
        "https://cdn.example.com/a", allowed_hosts=(".example.com",)
    )
    with pytest.raises(UnsafeExportURL):
        validate_export_url("https://evil.test/a", allowed_hosts=(".example.com",))
