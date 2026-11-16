"""Defense-in-depth validation for values the server stores verbatim.

The HTTP schemas validate hostile text and URLs before a request reaches the
manager, but the manager is a public Python API in its own right: fixtures,
tests, the gateway and internal callers invoke ``create_server`` /
``update_server`` directly. Keeping the same invariants at the core boundary
means a caller that bypasses FastAPI cannot persist an SSRF vector or a
control-character-laden name.

The helpers import (never redefine) the shared hardening rules so there is one
scheme allow-list, one SSRF gate and one control-character policy in the code
base.
"""

from __future__ import annotations

from typing import Optional, Union
from urllib.parse import urlparse

from .models import ChannelType
from src.core.security.hardening import HardeningViolation
from src.core.security.hardening.text import reject_control_characters
from src.core.security.hardening.urls import require_http_url
from src.core.security.outbound import parse_outbound_url

#: Longest description accepted at the core boundary. Matches the HTTP schema
#: so an internal caller cannot persist a description the API would reject.
MAX_SERVER_DESCRIPTION_LENGTH = 1000
#: A URL longer than this is never legitimate for an icon/banner. Capping it
#: also keeps a hostile value from being stored and echoed to every client.
MAX_SERVER_MEDIA_URL_LENGTH = 2048


def validate_server_media_url(
    value: Optional[str], field: str = "server media URL"
) -> Optional[str]:
    """Return a server icon/banner URL that is safe to persist verbatim.

    Uploaded icons are stored as same-origin paths (``/api/v1/avatars/...``).
    Those are safe by construction: the browser resolves them against our own
    origin and the server never fetches them. Everything else must be an
    absolute HTTP(S) URL that clears both the scheme allow-list and the shared
    SSRF gate (which resolves the host once and refuses private, loopback,
    link-local and otherwise forbidden destinations).

    ``HardeningViolation``/``OutboundUrlRejected`` are both ``ValueError``
    subclasses, so existing handlers keep treating a rejected value as a bad
    request.
    """
    if value is None:
        return None
    url = reject_control_characters(value, field).strip()
    if not url:
        return None
    if len(url) > MAX_SERVER_MEDIA_URL_LENGTH:
        raise HardeningViolation(f"{field} is too long")

    parsed = urlparse(url)
    # Same-origin absolute path: no scheme, no authority, no embedded
    # credentials, a single leading slash and no backslash tricks.
    if (
        url.startswith("/")
        and not url.startswith("//")
        and "\\" not in url
        and not parsed.scheme
        and not parsed.netloc
        and not parsed.username
        and not parsed.password
    ):
        return url

    # Remote URLs: enforce the scheme allow-list first (cheap, no DNS), then
    # the SSRF gate. parse_outbound_url re-checks the scheme itself, so the two
    # checks cannot drift silently.
    require_http_url(url, field)
    parse_outbound_url(url, field)
    return url


def validate_server_description(
    value: object, field: str = "Server description"
) -> Optional[str]:
    """Bound a server description and refuse embedded control characters.

    Descriptions are rendered to every member of a server. Enforcing the same
    length ceiling and control-character rule as the HTTP schema at the core
    boundary closes the direct-caller bypass.
    """
    if value is None:
        return None
    text = reject_control_characters(value, field)
    if len(text) > MAX_SERVER_DESCRIPTION_LENGTH:
        raise HardeningViolation(f"{field} is too long")
    return text


def coerce_channel_type(value: Union[ChannelType, str]) -> ChannelType:
    """Return the ChannelType enum for *value*, refusing bare/invalid strings.

    Callers used to pass any string and fall back to TEXT when it did not match,
    so a typo silently created a differently behaved channel. Accept a real
    ChannelType unchanged; for text, normalise case and require an exact member
    of the enum, otherwise raise ValueError at the boundary.
    """
    if isinstance(value, ChannelType):
        return value
    if not isinstance(value, str):
        raise ValueError("channel_type must be a string or ChannelType")
    normalized = reject_control_characters(value, "channel_type").strip().lower()
    if not normalized:
        raise ValueError("channel_type must not be empty")
    try:
        return ChannelType(normalized)
    except ValueError as exc:
        raise ValueError(
            "channel_type must be one of: " + ", ".join(t.value for t in ChannelType)
        ) from exc
