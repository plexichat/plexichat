"""Strict URL gate for every URL an embed can carry.

``validate_url`` already checks URL *shape* and runs the SSRF validator when
it is importable. That is not enough at the embedding boundary: a URL with
embedded credentials, a non-http(s) scheme or a missing host can survive the
shape regex, and the SSRF fallback only blocks raw IP literals when the
security module is unavailable. This module centralises the fail-closed gate
that all embed URL fields go through.
"""

from __future__ import annotations

from src.core.security.hardening import HardeningViolation, require_http_url

from .constants import MAX_URL_LENGTH
from ..exceptions import InvalidUrlError


def reject_non_http_url(value: object, field_name: str = "url") -> str:
    """Return ``value`` only if it is an absolute http(s) URL without creds.

    Uses the shared hardening primitive so the rule matches the rest of the
    codebase: http/https only, a hostname present, no ``user:pass@`` prefix.
    """
    try:
        return require_http_url(value, field_name)
    except HardeningViolation as exc:
        raise InvalidUrlError(f"{field_name}: {exc}", str(value or "")) from exc


def enforce_ssrf_gate(url: str, field_name: str = "url") -> str:
    """Run the centralised DNS/SSRF gate, failing closed when unavailable."""
    try:
        from src.utils.security import URLValidator
    except Exception as exc:
        raise InvalidUrlError(
            f"SSRF gate unavailable for {field_name}; refusing URL", url
        ) from exc
    try:
        URLValidator().validate_url_for_request(url)
    except Exception as exc:
        raise InvalidUrlError(
            f"URL failed SSRF check in {field_name}: {exc}", url
        ) from exc
    return url


def validate_embed_url(
    value: object,
    field_name: str = "url",
    *,
    allow_relative: bool = False,
) -> str:
    """Validate one embed URL field end-to-end.

    Args:
        value: Raw value from the untrusted embed payload.
        field_name: Label for error messages.
        allow_relative: Permit the strictly-shaped server-relative media path
            (no scheme gate applies to those; length is still bounded).

    Returns:
        The validated (possibly normalised) URL string.

    Raises:
        InvalidUrlError: On any non-http(s) scheme, credentials, oversize,
            failed SSRF check or invalid shape.
    """
    from .url_validation import validate_url

    text = str(value or "").strip()
    if not text:
        return text
    if len(text) > MAX_URL_LENGTH:
        raise InvalidUrlError(
            f"{field_name} exceeds maximum URL length of {MAX_URL_LENGTH}", text
        )

    if allow_relative and text.startswith("/"):
        # Relative media-pipeline paths have no scheme; validate_url enforces
        # the strict ``/api/v1/media/...`` shape and traversal rules.
        return validate_url(text, field_name)

    reject_non_http_url(text, field_name)
    enforce_ssrf_gate(text, field_name)
    return validate_url(text, field_name)
