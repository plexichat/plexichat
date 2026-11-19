"""Regression: backslash-in-authority URLs must not pass the export SSRF gate.

``urlsplit`` derives the host from the text to the RIGHT of an ``@`` in the
authority, but http(s) clients following the WHATWG URL standard read ``\\`` as
``/`` and connect to the address on the LEFT. Without an explicit authority
check, ``https://169.254.169.254\\@public.example/x`` is vetted as the public
host and then fetched at the link-local metadata address by any sink that
re-resolves the URL. The rest of the codebase (``outbound``, ``web_push``,
``URLValidator``) already rejects this shape; this gate must too.
"""

from __future__ import annotations

import socket

import pytest

from src.core.artifacts.export import UnsafeExportURL, validate_export_url


BACKSLASH_AUTHORITY_PAYLOADS = [
    r"https://169.254.169.254\@public.example/x",
    r"http://127.0.0.1\@example.com/x",
    r"http://metadata.google.internal\@example.com/computeMetadata/v1/",
    r"https://[::1]\@example.com/x",
    r"https://internal.service\public.example/x",
]


@pytest.mark.parametrize("url", BACKSLASH_AUTHORITY_PAYLOADS)
def test_backslash_in_authority_rejected_before_dns(url, monkeypatch):
    def no_dns(*args, **kwargs):
        raise AssertionError("DNS must not be consulted for a backslash-authority URL")

    monkeypatch.setattr(socket, "getaddrinfo", no_dns)
    with pytest.raises(UnsafeExportURL):
        validate_export_url(url)


def test_backslash_in_path_or_percent_encoded_is_still_allowed():
    # A literal backslash in the path is data, not an authority separator, and
    # a percent-encoded one never enters the authority at all. Both must remain
    # acceptable so the authority check is not over-broad.
    assert validate_export_url(r"https://93.184.216.34/a\b")
    assert validate_export_url("https://93.184.216.34/a%5Cb")
