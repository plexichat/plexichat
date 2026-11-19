"""Regression: voice-message storage URLs must not smuggle a host via '\\'.

The JSON voice-message route stores the client-supplied attachment URL verbatim
and hands it to other users' clients. ``urlparse`` derives the host from the
text to the RIGHT of an ``@`` in the authority, but browsers and http(s)
clients following WHATWG read ``\\`` as ``/`` and connect to the address on the
LEFT. A URL like ``https://169.254.169.254\\@public.example/x`` must be refused
here, not stored as if it pointed at a public host.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from src.api.routes.features.voice import _validate_voice_metadata

_VALID_KW = dict(
    filename="note.webm",
    content_type="audio/webm",
    size=1024,
    duration_ms=1000,
)


@pytest.mark.parametrize(
    "url",
    [
        r"https://169.254.169.254\@public.example/audio.webm",
        r"http://192.168.1.1\@example.com/audio.webm",
        r"http://127.0.0.1\@example.com/audio.webm",
        r"https://metadata.google.internal\@example.com/audio.webm",
        r"https://internal.service\public.example/audio.webm",
    ],
)
def test_backslash_authority_storage_url_rejected(url):
    with pytest.raises(HTTPException) as exc:
        _validate_voice_metadata(**_VALID_KW, url=url)
    assert exc.value.status_code == 400


def test_backslash_in_path_and_percent_encoded_backslash_still_allowed():
    # Backslashes that are data (path) or percent-encoded never enter the
    # authority and must keep working.
    _validate_voice_metadata(**_VALID_KW, url=r"https://93.184.216.34/a\b.webm")
    _validate_voice_metadata(
        **_VALID_KW, url="https://93.184.216.34/a%5Cb.webm"
    )


def test_local_storage_prefix_still_allowed():
    _validate_voice_metadata(**_VALID_KW, url="/api/v1/media/audio/abc.webm")
