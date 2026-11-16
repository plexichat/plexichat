"""Canonical secret comparison, scrubbing and HMAC token helpers.

Secret handling goes wrong in two common ways: a plain ``==`` on a token or
digest leaks its content through comparison timing, and an immutable ``bytes``
or ``str`` holding a secret is copied around forever with no way to zero it.
This module gives call sites one constant-time comparison function, a mutable
buffer that is scrubbed on exit, and token helpers built on both.

Python cannot reliably erase an immutable ``bytes``/``str`` - the "where
feasible" part is to keep transient secret material in a mutable ``bytearray``
and zero it as soon as the operation finishes. ``scrub_secret`` returns False
for immutable inputs so callers do not mistake a no-op for erasure.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any, Union

MIN_TOKEN_KEY_LENGTH = 16
MAX_TOKEN_KEY_LENGTH = 256
_SECRET_TYPES = (bytes, bytearray, memoryview)


def _as_bytes(value: Any, name: str) -> bytes:
    if isinstance(value, str):
        return value.encode("utf-8")
    if isinstance(value, _SECRET_TYPES):
        return bytes(value)
    raise TypeError(f"{name} must be str or bytes-like")


def constant_time_compare(left: Any, right: Any) -> bool:
    """Compare two secrets without revealing their content by timing.

    Strings are encoded explicitly so non-ASCII secrets work (passing ``str``
    directly to :func:`hmac.compare_digest` raises ``TypeError`` for anything
    outside ASCII). Non-secret types are rejected instead of being coerced.
    """
    return hmac.compare_digest(_as_bytes(left, "left"), _as_bytes(right, "right"))


def scrub_secret(value: Any) -> bool:
    """Best-effort zeroing of a mutable secret buffer.

    Returns True when the buffer was actually overwritten, False for immutable
    values or read-only views that cannot be scrubbed.
    """
    if isinstance(value, bytearray):
        value[:] = b"\x00" * len(value)
        return True
    if isinstance(value, memoryview):
        if value.readonly:
            return False
        try:
            value[:] = b"\x00" * len(value)
        except (TypeError, ValueError):
            return False
        return True
    return False


class SecretBuffer:
    """A mutable secret that is zeroed on ``clear()`` and context exit."""

    __slots__ = ("_buffer", "_scrubbed")

    def __init__(self, data: Union[str, bytes, bytearray]) -> None:
        if isinstance(data, str):
            data = data.encode("utf-8")
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("secret buffer data must be str or bytes-like")
        self._buffer = bytearray(data)
        self._scrubbed = False

    def _check(self) -> None:
        if self._scrubbed:
            raise ValueError("secret buffer has been scrubbed")

    @property
    def view(self) -> memoryview:
        """A writable view for APIs that fill or consume the buffer in place."""
        self._check()
        return memoryview(self._buffer)

    @property
    def bytes(self) -> bytes:
        """A snapshot of the current secret as immutable bytes."""
        self._check()
        return bytes(self._buffer)

    def clear(self) -> None:
        if not self._scrubbed:
            scrub_secret(self._buffer)
            self._scrubbed = True

    def __len__(self) -> int:
        return len(self._buffer)

    def __enter__(self) -> "SecretBuffer":
        self._check()
        return self

    def __exit__(self, *exc_info: Any) -> bool:
        self.clear()
        return False

    def __del__(self) -> None:  # pragma: no cover - GC timing is not deterministic
        try:
            self.clear()
        except Exception:
            pass


def _key_buffer(key: Any) -> SecretBuffer:
    if isinstance(key, SecretBuffer):
        key = key.bytes
    buffer = SecretBuffer(_as_bytes(key, "key"))
    if not MIN_TOKEN_KEY_LENGTH <= len(buffer) <= MAX_TOKEN_KEY_LENGTH:
        buffer.clear()
        raise ValueError("token key length is outside the allowed range")
    return buffer


def sign_token(payload: Union[str, bytes], key: Any) -> str:
    """Return a hex HMAC-SHA256 token for ``payload`` under ``key``."""
    payload_bytes = _as_bytes(payload, "payload")
    with _key_buffer(key) as key_buffer:
        return hmac.new(key_buffer.bytes, payload_bytes, hashlib.sha256).hexdigest()


def verify_token(payload: Union[str, bytes], signature: Any, key: Any) -> bool:
    """Verify an HMAC token in constant time, failing closed on bad input."""
    if not isinstance(signature, str):
        return False
    expected = sign_token(payload, key)
    # Compare fixed-length lower-case hex; reject any transport mangling.
    return constant_time_compare(expected, signature) and len(signature) == 64


def generate_token_key(length: int = 32) -> SecretBuffer:
    """Create a random token key inside a scrub-on-exit buffer."""
    if not MIN_TOKEN_KEY_LENGTH <= length <= MAX_TOKEN_KEY_LENGTH:
        raise ValueError("token key length is outside the allowed range")
    return SecretBuffer(os.urandom(length))


__all__ = [
    "MAX_TOKEN_KEY_LENGTH",
    "MIN_TOKEN_KEY_LENGTH",
    "SecretBuffer",
    "constant_time_compare",
    "generate_token_key",
    "scrub_secret",
    "sign_token",
    "verify_token",
]
