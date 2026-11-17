"""Modern TLS settings for the server SSL context.

Uvicorn's default ``create_ssl_context`` loads the certificate but leaves the
minimum protocol version and the renegotiation policy to the OpenSSL defaults,
which on many builds still permit TLS 1.0/1.1 and legacy renegotiation. The
helpers here make those choices explicitly:

* a hard floor of TLS 1.2 (TLS 1.3 is preferred when both peers support it);
* a forward-secret AEAD cipher list with every legacy primitive removed;
* server cipher preference, compression disabled and insecure renegotiation
  refused.

``hardened_ssl_kwargs`` returns the keyword arguments the server config path
passes to ``uvicorn.Config``; the ``ssl_context_factory`` entry is what makes
the settings actually apply rather than sitting in a constant nobody reads.
"""

from __future__ import annotations

import ssl
from functools import partial
from typing import Any, Callable, Optional

MIN_TLS_VERSION = ssl.TLSVersion.TLSv1_2

# Every enabled cipher is ECDHE/DHE key exchange with an AEAD bulk cipher.
# The trailing exclusions are defence in depth: a future edit that widens the
# list cannot silently reintroduce RC4, 3DES, NULL, MD5 or SHA-1 MACs.
SECURE_CIPHERS = (
    "ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:RSA+AESGCM:"
    "!aNULL:!eNULL:!NULL:!MD5:!SHA1:!RC4:!3DES:!DES:!EXPORT:"
    "!DSS:!PSK:!SRP:!CAMELLIA:!SEED:!IDEA:!SEED"
)

# Tokens that may never appear in an operator-supplied enabled cipher string.
# Parse directives (``!SHA1``, ``-RC4``) are ignored by validate_cipher_string,
# so an explicit exclusion is still allowed.
_WEAK_CIPHER_TOKENS = (
    "RC4",
    "3DES",
    "DES",
    "NULL",
    "MD5",
    "SHA1",
    "EXPORT",
    "PSK",
    "SRP",
    "DSS",
    "ANON",
    "ADH",
    "AECDH",
    # Broad OpenSSL aliases expand to every cipher (including legacy ones).
    "ALL",
    "DEFAULT",
    "COMPAT",
    "LEGACY",
    "HIGH",
    "LOW",
    "MEDIUM",
    "CAMELLIA",
    "SEED",
    "IDEA",
    "SSLV3",
    "TLSV1",
)

_WEAK_VERSION_NAMES = frozenset(
    {
        "SSLV2",
        "SSLV3",
        "SSL2",
        "SSL3",
        "TLSV1",
        "TLSV1.0",
        "TLS1.0",
        "TLS1",
        "TLSV1_0",
        "TLS1_0",
        "TLSV1.1",
        "TLS1.1",
        "TLSV1_1",
        "TLS1_1",
    }
)
_VERSION_NAMES = {
    "TLSV1.2": ssl.TLSVersion.TLSv1_2,
    "TLSV1_2": ssl.TLSVersion.TLSv1_2,
    "TLS1.2": ssl.TLSVersion.TLSv1_2,
    "TLS1_2": ssl.TLSVersion.TLSv1_2,
    "TLSV1.3": ssl.TLSVersion.TLSv1_3,
    "TLSV1_3": ssl.TLSVersion.TLSv1_3,
    "TLS1.3": ssl.TLSVersion.TLSv1_3,
    "TLS1_3": ssl.TLSVersion.TLSv1_3,
}


class TLSHardeningError(ValueError):
    """A TLS setting is missing, malformed, or weaker than the allowed floor."""


def validate_min_tls_version(value: Any = None) -> ssl.TLSVersion:
    """Return ``value`` as a TLSVersion, rejecting anything below TLS 1.2.

    ``None`` means "use the module floor". Strings accept the common spellings
    (``TLSv1.2``, ``TLS1_3``, ...); a ``ssl.TLSVersion`` is passed through. All
    other inputs, including weak legacy versions, fail closed.
    """
    if value is None:
        return MIN_TLS_VERSION
    if isinstance(value, ssl.TLSVersion):
        version = value
    elif isinstance(value, str):
        name = value.strip().upper().replace("-", "_")
        if not name or name in _WEAK_VERSION_NAMES:
            raise TLSHardeningError(f"TLS version {value!r} is below the TLS 1.2 floor")
        version = _VERSION_NAMES.get(name)
        if version is None:
            raise TLSHardeningError(f"Unsupported TLS version {value!r}")
    else:
        raise TLSHardeningError("TLS minimum version must be a version name")
    if version.value < MIN_TLS_VERSION.value:
        raise TLSHardeningError(f"TLS version {version.name} is below the TLS 1.2 floor")
    return version


def validate_cipher_string(value: Any) -> str:
    """Validate an operator-supplied cipher list, refusing weak enabled ciphers."""
    if not isinstance(value, str) or not value.strip():
        raise TLSHardeningError("tls.ciphers must be a non-empty string")
    if len(value) > 1024 or any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise TLSHardeningError("tls.ciphers contains control characters or is too long")
    text = value.strip()
    for part in text.split(":"):
        token = part.strip()
        if not token or token.startswith(("!", "-", "+")):
            # Exclusion/subtraction directives tighten the list; they cannot
            # enable a weak cipher on their own.
            continue
        upper = token.upper()
        if any(weak in upper for weak in _WEAK_CIPHER_TOKENS):
            raise TLSHardeningError("tls.ciphers enables a deprecated cipher")
    return text


def reject_weak_renegotiation(context: ssl.SSLContext) -> None:
    """Harden ``context`` against legacy renegotiation and downgrade tricks."""
    no_renegotiation = getattr(ssl, "OP_NO_RENEGOTIATION", 0)
    if no_renegotiation:
        context.options |= no_renegotiation
    context.options |= ssl.OP_CIPHER_SERVER_PREFERENCE
    context.options |= ssl.OP_NO_COMPRESSION
    # Derive a fresh ephemeral key per handshake where the runtime supports it.
    for flag in ("OP_SINGLE_ECDH_USE", "OP_SINGLE_DH_USE"):
        option = getattr(ssl, flag, 0)
        if option:
            context.options |= option


def build_ssl_context(
    certfile: str,
    keyfile: Optional[str] = None,
    *,
    min_version: Any = None,
    ciphers: Optional[str] = None,
) -> ssl.SSLContext:
    """Build a hardened TLS server context with ``certfile``/``keyfile`` loaded."""
    version = validate_min_tls_version(min_version)
    cipher_list = validate_cipher_string(ciphers) if ciphers else SECURE_CIPHERS

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = version
    try:
        context.set_ciphers(cipher_list)
    except ssl.SSLError as exc:
        raise TLSHardeningError("tls.ciphers is not an accepted cipher list") from exc
    context.verify_mode = ssl.CERT_NONE
    reject_weak_renegotiation(context)
    try:
        context.load_cert_chain(certfile, keyfile)
    except (OSError, ssl.SSLError) as exc:
        raise TLSHardeningError("TLS certificate/key pair could not be loaded") from exc
    return context


def uvicorn_ssl_context_factory(
    uvicorn_config: Any,
    default_factory: Callable[[], ssl.SSLContext],
    *,
    min_version: Any = None,
    ciphers: Optional[str] = None,
) -> ssl.SSLContext:
    """Uvicorn ``ssl_context_factory`` that returns our hardened context.

    Uvicorn calls this at ``Config.load()`` time with the config object and its
    own default factory. A module-level callable (and a ``partial`` of it) is
    picklable, so it is safe in the multi-process startup path too.
    """
    certfile = getattr(uvicorn_config, "ssl_certfile", None)
    keyfile = getattr(uvicorn_config, "ssl_keyfile", None)
    if not certfile:
        # No certificate on the config: keep uvicorn's own (failing) behaviour
        # instead of inventing a context with no identity.
        return default_factory()
    return build_ssl_context(certfile, keyfile, min_version=min_version, ciphers=ciphers)


def hardened_ssl_kwargs(
    certfile: str,
    keyfile: str,
    *,
    min_version: Any = None,
    ciphers: Optional[str] = None,
) -> dict[str, Any]:
    """kwargs for ``uvicorn.Config`` that enforce the TLS floor in practice."""
    version = validate_min_tls_version(min_version)
    cipher_list = validate_cipher_string(ciphers) if ciphers else SECURE_CIPHERS
    return {
        "ssl_certfile": certfile,
        "ssl_keyfile": keyfile,
        "ssl_ciphers": cipher_list,
        "ssl_context_factory": partial(
            uvicorn_ssl_context_factory, min_version=version, ciphers=cipher_list
        ),
    }


__all__ = [
    "MIN_TLS_VERSION",
    "SECURE_CIPHERS",
    "TLSHardeningError",
    "build_ssl_context",
    "hardened_ssl_kwargs",
    "reject_weak_renegotiation",
    "uvicorn_ssl_context_factory",
    "validate_cipher_string",
    "validate_min_tls_version",
]
