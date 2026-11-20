"""Security boundary for artifact exports.

Exports turn two pieces of untrusted input into powerful side effects:

* a human supplied artifact **title** is copied into a ``Content-Disposition``
  response header. Without sanitising it, ``../`` can steer where a browser
  saves the file and CR/LF or quotes can inject extra header fields.
* rendered output has no natural size bound, so a single hostile artifact can
  exhaust server memory or be streamed back indefinitely.
* export jobs may be told to fetch their source from a URL. Any
  ``http(s)://`` URL is not safe: loopback, link-local and cloud metadata
  addresses turn the exporter into an SSRF proxy.

The helpers here avoid third-party dependencies so routes, workers and CLI
tools can all share the same checks. They fail closed: every function either
returns a value that is safe to use verbatim or raises a specific ``ValueError``
subclass. The IP policy intentionally reuses the central ``URLValidator``.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import unicodedata
from collections.abc import Iterable, Iterator
from typing import Any
from urllib.parse import quote, urlsplit

# Keep these allowlists explicit instead of accepting arbitrary extensions:
# an unexpected extension can change the MIME type the browser sees.
EXPORT_FORMATS = frozenset(
    {
        "html",
        "pdf",
        "md",
        "odt",
        "txt",
        "plexiscribe",
        "plexiscript",
        "plexiboard",
    }
)

MAX_FILENAME_LEN = 80
"""Maximum length of the *stem* (the title before the format extension)."""

MAX_EXPORT_BYTES = 32 * 1024 * 1024
"""Default ceiling for a single rendered export (32 MiB)."""

MAX_EXPORT_CHARS = 4_000_000
"""Default ceiling for the plain text fed into a renderer."""

_EXPORT_CHUNK_BYTES = 64 * 1024

# ASCII controls, DEL/C1 controls, and the Unicode bidirectional overrides that
# can make a filename look harmless while rendering as something else.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f\u200e\u200f\u202a-\u202e\u2066-\u2069]")
_UNSAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_HEADER_FORBIDDEN_RE = re.compile(r'[\r\n"\\/;]')
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class UnsafeExportInput(ValueError):
    """Raised when an export input cannot be made safe."""


class ExportTooLarge(UnsafeExportInput):
    """Raised when an export exceeds a configured size budget."""


class UnsafeExportURL(UnsafeExportInput):
    """Raised when an export source URL is unsafe to fetch."""


def _clean_stem(title: Any, max_stem_len: int) -> str:
    """Reduce an arbitrary title to a single, bounded filename stem."""
    if max_stem_len <= 0:
        raise ValueError("max_stem_len must be positive")

    text = "" if title is None else str(title)
    text = _CONTROL_RE.sub("", text)
    # Normalise first (compat decompositions can introduce '/'), then strip
    # controls again in case a compatibility character expanded into one.
    text = unicodedata.normalize("NFKC", text)
    text = _CONTROL_RE.sub("", text)

    # Treat backslashes as separators too: Windows clients interpret them as
    # path components even though the server may run on POSIX.
    text = text.replace("\\", "/").rsplit("/", 1)[-1]
    text = _UNSAFE_FILENAME_RE.sub("-", text).strip("-._")

    # Collapse repeated dots so ".." can never survive as a path component.
    while ".." in text:
        text = text.replace("..", ".")
    text = text.strip("-._")[:max_stem_len].strip("-._")
    if not text or text in {".", ".."}:
        return "export"
    return text


def safe_export_filename(
    title: Any,
    export_format: str,
    *,
    max_stem_len: int = MAX_FILENAME_LEN,
) -> tuple[str, str]:
    """Return ``(ascii_filename, utf8_quoted_filename)`` for a download.

    The ASCII form is safe to place inside a quoted ``filename=`` parameter;
    the second form is RFC 5987 encoded and preserves non-ASCII titles.
    Neither form can contain a path separator, a quote, CR/LF, or a raw
    percent-encoded traversal sequence.
    """
    fmt = str(export_format or "").strip().lower()
    if fmt not in EXPORT_FORMATS:
        raise UnsafeExportInput("Unsupported export format")

    stem = _clean_stem(title, max_stem_len)

    # Build the ASCII fallback from the *stem* so a non-ASCII title cannot
    # collapse the filename into a bare ".html".
    ascii_stem = stem.encode("ascii", "ignore").decode("ascii").strip("-._")
    if not ascii_stem:
        ascii_stem = "export"
    ascii_name = f"{ascii_stem}.{fmt}"
    ascii_name = _HEADER_FORBIDDEN_RE.sub("", ascii_name).strip(". _")
    if not ascii_name or "." not in ascii_name:
        ascii_name = f"export.{fmt}"

    # safe="" also percent-encodes apostrophes (the RFC 5987 delimiter),
    # percent signs, slashes and control characters.
    utf8_name = quote(f"{stem}.{fmt}", safe="")
    return ascii_name, utf8_name


def content_disposition_header(
    title: Any,
    export_format: str,
    *,
    inline: bool = False,
    max_stem_len: int = MAX_FILENAME_LEN,
) -> str:
    """Build a complete ``Content-Disposition`` value from an untrusted title."""
    ascii_name, utf8_name = safe_export_filename(
        title, export_format, max_stem_len=max_stem_len
    )
    kind = "inline" if inline else "attachment"
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_name}"


def enforce_export_limit(
    data: bytes | bytearray, *, max_bytes: int | None = None
) -> bytes:
    """Return ``data`` only if it fits the export byte budget."""
    limit = MAX_EXPORT_BYTES if max_bytes is None else max_bytes
    if limit < 0:
        raise ValueError("max_bytes must not be negative")
    if len(data) > limit:
        raise ExportTooLarge(
            f"Export exceeds the {limit} byte limit ({len(data)} bytes)"
        )
    return bytes(data)


def bound_export_text(text: str, *, max_chars: int | None = None) -> str:
    """Reject oversized text before it is fed into a renderer."""
    limit = MAX_EXPORT_CHARS if max_chars is None else max_chars
    if limit < 0:
        raise ValueError("max_chars must not be negative")
    if len(text) > limit:
        raise ExportTooLarge(
            f"Export text exceeds the {limit} character limit ({len(text)} chars)"
        )
    return text


def bounded_chunks(
    data: bytes | bytearray,
    *,
    chunk_size: int = _EXPORT_CHUNK_BYTES,
    max_bytes: int | None = None,
) -> Iterator[bytes]:
    """Yield bounded slices, refusing to emit output larger than the budget.

    The whole payload is checked before the first chunk is yielded so a caller
    cannot commit a 200 response and then discover the export is too large.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    enforce_export_limit(data, max_bytes=max_bytes)
    for offset in range(0, len(data), chunk_size):
        yield bytes(data[offset : offset + chunk_size])


def _address_is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Fail closed for every address class usable for SSRF.

    The IP policy comes from the central ``URLValidator`` so this hand-rolled
    gate cannot drift from every other outbound boundary. ``ipaddress`` alone
    is not enough: ``ipaddress.is_global`` does not flag deprecated IPv6
    site-local space (``fec0::/10``) or every NAT64 transition address. An
    export source URL naming such an address
    used to be vetted as public and fetched from inside the site. If the central
    gate cannot be imported, fail closed rather than vetting an unverified IP.
    """
    try:
        from src.utils.security import URLValidator
    except Exception:
        return False
    return not URLValidator().is_private_ip(str(ip))


def _host_matches_allowlist(host: str, allowed_hosts: Iterable[str] | None) -> bool:
    if allowed_hosts is None:
        return True
    host = host.rstrip(".").lower()
    for entry in allowed_hosts:
        candidate = str(entry).rstrip(".").lower()
        if not candidate:
            continue
        if candidate.startswith("."):
            if host == candidate[1:] or host.endswith(candidate):
                return True
        elif host == candidate:
            return True
    return False


def vetted_export_addresses(
    url: str,
    *,
    allowed_schemes: Iterable[str] = ("https", "http"),
    allowed_hosts: Iterable[str] | None = None,
    allow_private: bool = False,
) -> tuple[str, ...]:
    """Resolve ``url`` and return the public IPs it points at.

    Every answer from DNS must be public: a single loopback record in a mixed
    response is enough to reject the URL, which closes the common DNS-rebinding
    and split-horizon bypasses. Callers should pin the connection to one of the
    returned addresses (or re-run the check immediately before connecting).
    """
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url):
        raise UnsafeExportURL("Control characters in export URL")

    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise UnsafeExportURL("Malformed export URL") from exc

    scheme = (parsed.scheme or "").lower()
    schemes = {str(s).lower() for s in allowed_schemes}
    if scheme not in schemes:
        raise UnsafeExportURL("Unsupported export URL scheme")

    if parsed.netloc and "@" in parsed.netloc:
        raise UnsafeExportURL("Credentials in export URL are not allowed")

    # A literal backslash in the authority is a parser differential between
    # urlsplit (which derives the host from the text to the RIGHT of an '@')
    # and http(s) clients following the WHATWG URL standard (which treat '\'
    # as '/' and connect to the address on the LEFT). Without this check,
    # ``https://169.254.169.254\@public.example/x`` is vetted as the public
    # host but fetched at the link-local metadata address by any sink that
    # re-resolves the URL. A literal backslash has no legitimate place in an
    # authority: data meant as data must be percent-encoded (``%5C``), which
    # keeps it out of the authority. Mirrors outbound.parse_outbound_url,
    # web_push._require_public_endpoint and URLValidator.
    if "\\" in (parsed.netloc or ""):
        raise UnsafeExportURL("Backslash in export URL authority is not allowed")

    host = parsed.hostname
    if not host:
        raise UnsafeExportURL("Export URL has no host")

    try:
        port = parsed.port
    except ValueError as exc:
        raise UnsafeExportURL("Invalid export URL port") from exc
    if port == 0:
        raise UnsafeExportURL("Invalid export URL port")

    if not _host_matches_allowlist(host, allowed_hosts):
        raise UnsafeExportURL("Export URL host is not allowlisted")

    # Literal IPs need no DNS lookup, but still get the same policy.
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        if not allow_private and not _address_is_public(literal):
            raise UnsafeExportURL("Export URL target address is not public")
        return (str(literal),)

    try:
        answers = socket.getaddrinfo(host, port or None, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as exc:
        raise UnsafeExportURL("Could not resolve export URL host") from exc
    if not answers:
        raise UnsafeExportURL("Export URL host did not resolve")

    vetted: list[str] = []
    for family, _type, _proto, _canon, sockaddr in answers:
        try:
            address = ipaddress.ip_address(sockaddr[0])
        except (ValueError, IndexError) as exc:
            raise UnsafeExportURL("Invalid resolved address") from exc
        if family not in (socket.AF_INET, socket.AF_INET6):
            raise UnsafeExportURL("Unsupported resolved address family")
        if not allow_private and not _address_is_public(address):
            raise UnsafeExportURL("Export URL resolves to a non-public address")
        vetted.append(str(address))
    return tuple(dict.fromkeys(vetted))


def validate_export_url(url: str, **kwargs: Any) -> str:
    """Return ``url`` unchanged when it is safe to fetch, else raise."""
    vetted_export_addresses(url, **kwargs)
    return url
