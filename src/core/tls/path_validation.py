"""Confinement and token checks for configured certificate paths.

``tls.cert_path`` / ``tls.key_path`` are operator-supplied strings that end up
in file reads and in uvicorn's SSL context. A path with ``..`` components, a
symlink that escapes the certificate directory, an embedded NUL/CRLF, or a
surprise suffix can point the server at an unintended file. These helpers
resolve the path and require it to stay inside an allowed root before use.

The confinement uses ``os.path.realpath`` + ``os.path.commonpath`` (not a
string prefix check), so sibling directories such as ``<root>-evil`` and
symlinked escapes are both rejected.
"""

from __future__ import annotations

import os
from typing import Iterable

from src.core.security.hardening import reject_control_characters

DEFAULT_SUFFIXES = (".pem", ".crt", ".cer", ".key")
MAX_PATH_LENGTH = 4096
_FORBIDDEN_TOKENS = ("\x00", "\r", "\n")


class TLSPathError(ValueError):
    """A certificate/key path failed confinement or format validation."""


def _roots(resolved_roots: Iterable[str]) -> list[str]:
    return [os.path.realpath(str(root)) for root in resolved_roots]


def _confined(candidate: str, allowed_roots: Iterable[str]) -> bool:
    for root in _roots(allowed_roots):
        try:
            if os.path.commonpath([root, candidate]) == root:
                return True
        except ValueError:
            # Different drives (Windows) -- not confined.
            continue
    return False


def validate_cert_path(
    path: object,
    *,
    allowed_roots: Iterable[str],
    expected_suffixes: Iterable[str] = DEFAULT_SUFFIXES,
    max_length: int = MAX_PATH_LENGTH,
) -> str:
    """Resolve ``path`` and require it to be confined, token-clean and suffixed."""
    if not isinstance(path, str) or not path.strip():
        raise TLSPathError("certificate path must be a non-empty string")
    if len(path) > max_length:
        raise TLSPathError("certificate path is too long")
    for token in _FORBIDDEN_TOKENS:
        if token in path:
            raise TLSPathError("certificate path contains a forbidden character")
    # Reuse the shared control-character rule for the rest of C0/DEL.
    try:
        reject_control_characters(path, field="tls path")
    except ValueError as exc:
        raise TLSPathError("certificate path contains a control character") from exc

    suffixes = tuple(expected_suffixes)
    lowered = path.lower()
    if suffixes and not any(lowered.endswith(suffix) for suffix in suffixes):
        raise TLSPathError("certificate path has an unexpected suffix")

    resolved = os.path.realpath(os.path.abspath(path))
    if not _confined(resolved, allowed_roots):
        raise TLSPathError("certificate path escapes its allowed directory")
    return resolved


def validate_certificate_pair(
    cert_path: object,
    key_path: object,
    **kwargs,
) -> tuple[str, str]:
    """Validate both paths and require them to resolve to different files."""
    cert = validate_cert_path(cert_path, **kwargs)
    key = validate_cert_path(key_path, **kwargs)
    if cert == key:
        raise TLSPathError("certificate and private key must be different files")
    return cert, key


def validate_config_path_tokens(path: object, *, field: str = "certificate path") -> str:
    """Reject traversal / control-character / over-long tokens in a config path.

    This is the *pre-resolution* check applied to operator-supplied config
    values before they reach ``os.path.abspath``/``realpath``. Once a path has
    been normalised a ``..`` component is no longer visible, so it must be
    rejected on the raw string. It complements :func:`validate_cert_path`'s
    post-resolution confinement (which needs an explicit allowed root that a
    TLS startup config does not always have) and shares its token rules.
    """
    if not isinstance(path, str) or not path.strip():
        raise TLSPathError(f"{field} must be a non-empty string")
    if len(path) > MAX_PATH_LENGTH:
        raise TLSPathError(f"{field} is too long")
    for token in _FORBIDDEN_TOKENS:
        if token in path:
            raise TLSPathError(f"{field} contains a forbidden character")
    try:
        reject_control_characters(path, field=field)
    except ValueError as exc:
        raise TLSPathError(f"{field} contains a control character") from exc
    # Normalise the separator so Windows-style ``..\\`` is caught too.
    if any(part == ".." for part in path.replace("\\", "/").split("/")):
        raise TLSPathError(f"{field} must not contain '..' traversal")
    return path


__all__ = [
    "DEFAULT_SUFFIXES",
    "MAX_PATH_LENGTH",
    "TLSPathError",
    "validate_cert_path",
    "validate_certificate_pair",
    "validate_config_path_tokens",
]
