"""Browser-normalisation-aware scheme guard for user-supplied media URLs.

Sound and sticker URLs end up in browser media elements (``<audio src>``,
``<img src>``) and, in a number of clients, in generated HTML/Markdown. A
validator that inspects the raw string is therefore not enough: browsers
decode HTML character references in attribute values and normalise control
characters before they determine a URL's scheme.

For example, all of the following are seen as ``javascript:`` by a browser
even though a naive ``str.startswith('javascript:')`` check misses them::

    javascript&#58;alert(1)      # HTML numeric entity for ':'
    javascript&colon;alert(1)    # HTML named entity for ':'
    &#106;avascript:alert(1)     # entity in the scheme name
    java\\nscript:alert(1)       # WHATWG strips ASCII tab/newline
    \\x01javascript:alert(1)     # WHATWG strips leading C0 controls

This module derives the verdict from a *normalised copy* of the URL so those
obfuscations cannot smuggle a script-bearing scheme past the validators.
The stored value is never rewritten -- only the security decision uses the
normalised form.
"""

from __future__ import annotations

import html
import re

# The WHATWG URL parser discards ASCII tab/newline and leading/trailing C0
# control characters before parsing the scheme.
_URL_IGNORED_RE = re.compile(r"[\x00-\x20\x7f]")

# A colon that appears before any path/query/fragment separator denotes an
# explicit scheme. Backslash is included because browsers treat it as a path
# separator for special (http/https/file) schemes.
_SCHEME_BOUNDARY_CHARS = ("/", "?", "#", "\\")


def normalized_url(url: str) -> str:
    """Return the URL as a browser would see it before scheme resolution.

    HTML character references are decoded once (attribute-value semantics),
    then tabs/newlines/C0 controls are removed (WHATWG URL semantics).
    """
    decoded = html.unescape(url)
    return _URL_IGNORED_RE.sub("", decoded)


def url_scheme(url: str) -> str | None:
    """Return the lower-case explicit scheme of ``url``, or ``None``.

    The scheme is taken from the browser-normalised form, so entity- or
    control-character-obfuscated schemes are reported accurately.
    """
    normalized = normalized_url(url)
    end = len(normalized)
    for char in _SCHEME_BOUNDARY_CHARS:
        idx = normalized.find(char)
        if idx != -1 and idx < end:
            end = idx
    prefix = normalized[:end]
    colon = prefix.find(":")
    if colon == -1:
        return None
    return prefix[:colon].lower()


def is_safe_media_url(url: object, *, allow_relative: bool = True) -> bool:
    """True when ``url`` cannot resolve to a non-http(s) scheme in a browser.

    Only absolute ``http://`` / ``https://`` URLs are accepted as absolute
    values. Schemeless values (relative paths, bare host/path strings) are
    accepted only when ``allow_relative`` is true; protocol-relative URLs
    (``//host``, ``/\\host``) are always refused because browsers resolve them
    against the ambient origin. Any explicit scheme other than http/https is
    rejected, using the normalised form so obfuscations cannot bypass it.
    """
    if not isinstance(url, str) or not url.strip():
        return False
    normalized = normalized_url(url)
    if not normalized:
        return False
    # Protocol-relative URLs inherit the page's scheme/origin; treat them as
    # unsafe rather than letting them silently point at another host. For
    # special schemes (http/https/file) the WHATWG parser treats a backslash
    # exactly like a forward slash, so "\\host" and "\/host" are network-path
    # references just as dangerous as "//host". Reject any leading pair of
    # separators in either orientation.
    if normalized[:1] in ("/", "\\") and normalized[1:2] in ("/", "\\"):
        return False
    scheme = url_scheme(url)
    if scheme is not None:
        # The scheme is matched case-insensitively (as browsers do), so keep
        # accepting legitimate upper-case ``HTTPS://`` values.
        return scheme in ("http", "https") and normalized.lower().startswith(
            ("http://", "https://")
        )
    if normalized.startswith("/"):
        return allow_relative
    return allow_relative


__all__ = ["normalized_url", "url_scheme", "is_safe_media_url"]
