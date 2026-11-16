"""Boundary guards for voice server/link URLs and session metadata.

The signaling layer reads STUN/TURN URLs and endpoints from operator config and
returns them to every client that joins voice. A single injected URL can point
a browser at an attacker-controlled relay (or carry a ``javascript:``/data
payload into a client attribute), so every URL is scheme-checked and length-
bounded at the response boundary, never trusted from config verbatim.
"""

from __future__ import annotations

from typing import Any, Iterable, List, Optional

from src.core.security.hardening import HardeningViolation, require_http_url

#: A client only needs a handful of ICE endpoints; cap the fan-out.
MAX_ICE_SERVERS = 16
MAX_ICE_URLS_PER_SERVER = 8
MAX_ICE_URL_LENGTH = 512
MAX_ENDPOINT_URL_LENGTH = 512
MAX_ICE_CREDENTIAL_LENGTH = 256

ICE_SCHEMES = frozenset({"stun", "stuns", "turn", "turns"})
DEFAULT_PUBLIC_STUN_URLS = [
    "stun:stun.l.google.com:19302",
    "stun:stun1.l.google.com:19302",
]

# Characters that can break out of a quoted client attribute or smuggle markup
# into an echo of the URL.
_UNSAFE_URL_CHARS = frozenset("<>\"'`\\")


def validate_ice_url(value: object, field: str = "ice_url") -> str:
    """Return ``value`` if it is a bounded stun/stuns/turn/turns URL.

    Raises ``ValueError`` otherwise. ICE URLs use the ``scheme:host:port`` form
    (``urlparse`` has no hostname for them), so the host is parsed directly.
    """
    text = str(value or "").strip()
    if not text or len(text) > MAX_ICE_URL_LENGTH:
        raise ValueError(f"{field} is empty or too long")
    if any(ch in text for ch in _UNSAFE_URL_CHARS):
        raise ValueError(f"{field} contains unsafe characters")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        raise ValueError(f"{field} contains control characters")

    scheme, sep, rest = text.partition(":")
    if not sep or scheme.lower() not in ICE_SCHEMES:
        raise ValueError(f"{field} must use a stun/turn scheme")
    # credentials in the URL ("user:pass@host") are not legitimate here
    if "@" in rest:
        raise ValueError(f"{field} must not embed credentials")
    host = (
        rest.split("]", 1)[0] + "]" if rest.startswith("[") else rest.split(":", 1)[0]
    )
    if not host or host.startswith("["):
        if not (rest.startswith("[") and "]" in rest):
            raise ValueError(f"{field} has no host")
    return text


def filter_ice_urls(urls: Iterable[object]) -> List[str]:
    """Keep only valid ICE URLs; fall back to public STUN if none remain."""
    valid: List[str] = []
    for url in urls or ():
        try:
            valid.append(validate_ice_url(url))
        except ValueError:
            continue
    return valid[:MAX_ICE_URLS_PER_SERVER] or list(DEFAULT_PUBLIC_STUN_URLS)


def validate_server_endpoint(value: object, field: str = "endpoint") -> str:
    """Return ``value`` only if it is a bounded absolute http(s) URL."""
    text = str(value or "").strip()
    if len(text) > MAX_ENDPOINT_URL_LENGTH:
        raise ValueError(f"{field} is too long")
    try:
        return require_http_url(text, field)
    except HardeningViolation as exc:
        raise ValueError(str(exc)) from exc


def _bounded_credential(value: object, field: str) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    if len(text) > MAX_ICE_CREDENTIAL_LENGTH:
        raise ValueError(f"{field} is too long")
    return text


def sanitize_ice_servers(servers: Iterable[Any]) -> List[dict]:
    """Validate and cap the ICE server list built from operator config.

    Returns plain dicts ready for the API schema. Invalid URLs are dropped and
    a bad credential makes that server fail closed rather than leaking to the
    client. An empty result is replaced with public STUN defaults by callers.
    """
    result: List[dict] = []
    for server in servers or ():
        if len(result) >= MAX_ICE_SERVERS:
            break
        raw_urls = getattr(server, "urls", ()) or ()
        if isinstance(raw_urls, str):
            raw_urls = [raw_urls]
        urls: List[str] = []
        for raw in raw_urls:
            try:
                candidate = validate_ice_url(raw)
            except ValueError:
                continue
            if candidate not in urls:
                urls.append(candidate)
            if len(urls) >= MAX_ICE_URLS_PER_SERVER:
                break
        if not urls:
            continue
        username = _bounded_credential(getattr(server, "username", None), "username")
        credential = _bounded_credential(
            getattr(server, "credential", None), "credential"
        )
        result.append({"urls": urls, "username": username, "credential": credential})
    return result



# --- session/participant metadata (commit 6) ---
MAX_SESSION_ID_LENGTH = 128
MAX_SFU_ID_LENGTH = 128
MAX_PARTICIPANTS_PER_CHANNEL = 100

_INVISIBLE_ID_CHARS = frozenset(
    {0x200B, 0x200C, 0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D,
     0x202E, 0x2066, 0x2067, 0x2068, 0x2069, 0xFEFF}
)


def validate_text_identifier(value: object, field: str, max_length: int) -> str:
    """Return a bounded, control-free text identifier."""
    text = str(value or "")
    if len(text) > max_length:
        raise ValueError(f"{field} exceeds maximum length of {max_length}")
    for ch in text:
        code = ord(ch)
        if code < 0x20 or code == 0x7F or code in _INVISIBLE_ID_CHARS:
            raise ValueError(f"{field} contains invalid characters")
    return text


def validate_session_id(value: object, field: str = "session_id") -> str:
    """Validate a voice session id returned to clients or stored on a connection."""
    return validate_text_identifier(value, field, MAX_SESSION_ID_LENGTH)


def validate_sfu_identifier(value: object, field: str = "sfu_id") -> str:
    """Validate an SFU-scoped room/peer/transport identifier."""
    return validate_text_identifier(value, field, MAX_SFU_ID_LENGTH)


def clamp_user_limit(value: object, field: str = "user_limit") -> int:
    """Clamp a stored or supplied channel user limit into a sane range."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(number, MAX_PARTICIPANTS_PER_CHANNEL))


def clamp_participant_count(value: object) -> int:
    """Clamp a participant count reported to clients."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(number, MAX_PARTICIPANTS_PER_CHANNEL))
