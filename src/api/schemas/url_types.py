"""Annotated URL types: one structural rule for every URL field.

A URL that reaches a schema is either stored and echoed to a browser or fetched
on a user's behalf. Before this module each field either had no rule at all or
grew its own ``urlparse`` check, so credentials (``http://user:pass@host``),
control characters and unbounded lengths slipped through wherever a call site
forgot a check. These types apply four structural constraints in one place:

* absolute ``http``/``https`` only (no ``javascript:``, ``data:``, relative paths);
* no embedded credentials;
* a bounded length;
* no control characters or bidirectional text overrides.

For the fields whose host is fixed by the API contract,
:func:`allowlisted_http_url` adds a host allowlist. SSRF resolution for URLs the
server actually fetches stays in ``src.core.security.outbound`` -- these types
only decide whether a value is a well-formed URL at all.
"""

from __future__ import annotations

from typing import Annotated, Iterable, cast

from pydantic import AfterValidator

from src.core.security.errors import OutboundUrlRejected
from src.core.security.hardening.urls import require_http_url

from .policy_types import reject_unsafe_unicode

#: Upper bound for a URL carried in a request field.
MAX_URL_LENGTH = 2048


def validate_http_url(
    value: object,
    field: str = "url",
    *,
    allowed_hosts: Iterable[str] | None = None,
) -> str:
    """Return ``value`` if it is a safe absolute HTTP(S) URL, else raise.

    ``None``/empty are returned unchanged so optional fields can be cleared;
    ``Field(min_length=1)`` is used on required fields to reject an empty value.
    """
    if value is None or value == "":
        return cast(str, value)
    text = str(value).strip()
    if len(text) > MAX_URL_LENGTH:
        raise OutboundUrlRejected(
            f"{field} must be at most {MAX_URL_LENGTH} characters", field=field
        )
    # Reuse the text policy's shared control/bidi rule rather than growing a
    # second character list that can drift out of sync with it.
    reject_unsafe_unicode(text, field)
    # Browsers and HTTP clients normalise a raw ``\`` to ``/`` in special
    # schemes (http/https/ws/wss), but ``urlparse`` does not. That parser
    # differential lets a value validated against one authority be fetched or
    # rendered with another -- e.g. an allowlisted host followed by
    # ``\@attacker.tld`` reads as a different host in a browser, and
    # ``https://example.com\evil.tld`` is navigated to as
    # ``https://example.com/evil.tld``. A literal backslash in a URL is
    # anomalous and must be percent-encoded (``%5C``) by the caller, so refuse
    # it outright rather than trusting two parsers to agree.
    if "\\" in text:
        raise OutboundUrlRejected(
            f"{field} must not contain backslashes", field=field
        )
    # require_http_url enforces absolute http(s) + no embedded credentials.
    require_http_url(text, field)
    if allowed_hosts is not None:
        from urllib.parse import urlparse

        parsed = urlparse(text)
        host = (parsed.hostname or "").lower()
        allow = {item.lower() for item in allowed_hosts}
        if host not in allow:
            raise OutboundUrlRejected(
                f"{field} host is not in the allowlist", field=field
            )
    return text


def http_url(policy_name: str = "http_url") -> AfterValidator:
    """Build the ``AfterValidator`` shared by all plain URL fields.

    The policy name is attached to the function so the schema coverage test can
    attribute an annotation to the URL rule just as it does for text policies.
    """
    check = _make_check(policy_name, allowed_hosts=None)
    return AfterValidator(check)


def allowlisted_http_url(*hosts: str, policy_name: str = "http_url_allowlisted"):
    """Build a URL annotation that also restricts the host to ``hosts``."""

    def check(value: str) -> str:
        return validate_http_url(value, "This field", allowed_hosts=hosts)

    setattr(check, "__policy_name__", policy_name)
    setattr(check, "__allowed_hosts__", frozenset(host.lower() for host in hosts))
    return Annotated[str, AfterValidator(check)]


def _make_check(policy_name: str, allowed_hosts):
    def check(value: str) -> str:
        return validate_http_url(value, "This field", allowed_hosts=allowed_hosts)

    setattr(check, "__policy_name__", policy_name)
    return check


#: A well-formed, credential-free, absolute HTTP(S) URL of bounded length.
HttpUrl = Annotated[str, http_url()]
