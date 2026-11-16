"""Bounded, audited PBKDF2 key derivation.

The generic ``EncryptionManager.derive_key`` accepts whatever iteration count
and salt length a caller supplies. A tiny value silently produces a weak key;
a huge one turns one request into a CPU denial of service. This module is the
policy layer for derivations that cross a trust boundary:

* parameters are bounded *before* any hashing happens (fail closed);
* every derivation is audited with the parameters only - the password, salt
  and derived key are never written to a log record;
* a known-answer vector set pins the underlying primitive so a refactor cannot
  change its output unnoticed.

``pbkdf2_raw`` is the unpoliced primitive used by those vectors; application
code should call ``derive_key`` / ``verify_derived_key`` instead.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, replace
from typing import Any, Optional

import utils.logger as logger

from .audit import sanitise_audit_entry
from .hardening import bounded_int
from .secrets import constant_time_compare, scrub_secret

DEFAULT_ALGORITHM = "sha256"
SUPPORTED_ALGORITHMS = frozenset({"sha256", "sha512"})
MIN_ITERATIONS = 100_000
MAX_ITERATIONS = 2_000_000
MIN_SALT_LENGTH = 16
MAX_SALT_LENGTH = 64
MIN_KEY_LENGTH = 16
MAX_KEY_LENGTH = 128


class KDFParameterError(ValueError):
    """A key-derivation parameter is missing, malformed, or outside policy."""


@dataclass(frozen=True)
class KDFParams:
    """Policy-checked PBKDF2 parameters."""

    algorithm: str = DEFAULT_ALGORITHM
    iterations: int = MIN_ITERATIONS
    salt_length: int = 16
    dklen: int = 32


DEFAULT_PARAMS = KDFParams()


@dataclass(frozen=True)
class DerivedKey:
    """A derived key together with the salt and parameters that produced it."""

    key: bytes
    salt: bytes
    params: KDFParams


def validate_kdf_params(params: Any = None) -> KDFParams:
    """Coerce and bound ``params``; reject anything outside the safe envelope."""
    if params is None:
        return DEFAULT_PARAMS
    if not isinstance(params, KDFParams):
        raise KDFParameterError("KDF parameters must be a KDFParams instance")
    algorithm = str(params.algorithm).lower()
    if algorithm not in SUPPORTED_ALGORITHMS:
        raise KDFParameterError("unsupported KDF algorithm")
    try:
        iterations = bounded_int(
            params.iterations,
            "iterations",
            minimum=MIN_ITERATIONS,
            maximum=MAX_ITERATIONS,
        )
        salt_length = bounded_int(
            params.salt_length,
            "salt_length",
            minimum=MIN_SALT_LENGTH,
            maximum=MAX_SALT_LENGTH,
        )
        dklen = bounded_int(
            params.dklen, "dklen", minimum=MIN_KEY_LENGTH, maximum=MAX_KEY_LENGTH
        )
    except ValueError as exc:
        raise KDFParameterError("KDF parameter is outside the allowed range") from exc
    return replace(
        params,
        algorithm=algorithm,
        iterations=iterations,
        salt_length=salt_length,
        dklen=dklen,
    )


def pbkdf2_raw(
    password: bytes,
    salt: bytes,
    iterations: int,
    dklen: int,
    algorithm: str = DEFAULT_ALGORITHM,
) -> bytes:
    """Unpoliced PBKDF2 primitive (used by the known-answer vectors).

    Only structural sanity is enforced here; policy belongs in
    ``validate_kdf_params``. Callers handling real secrets must use
    ``derive_key``.
    """
    if not isinstance(password, (bytes, bytearray)) or not password:
        raise KDFParameterError("password must be non-empty bytes")
    if not isinstance(salt, (bytes, bytearray)) or len(salt) < 1:
        raise KDFParameterError("salt must be non-empty bytes")
    if algorithm not in {"sha1", "sha256", "sha512"}:
        raise KDFParameterError("unsupported PBKDF2 hash")
    try:
        count = bounded_int(iterations, "iterations", minimum=1, maximum=10_000_000)
        length = bounded_int(dklen, "dklen", minimum=1, maximum=1024)
    except ValueError as exc:
        raise KDFParameterError("invalid PBKDF2 iteration count or length") from exc
    return hashlib.pbkdf2_hmac(
        algorithm, bytes(password), bytes(salt), count, dklen=length
    )


def audit_kdf_params(params: KDFParams) -> dict[str, str]:
    """Return a bounded, redaction-checked audit record for a derivation.

    Only the parameter names and their numeric values are present. The audit
    helper redacts anything keyed like a secret and bounds every value, so a
    future edit that tries to add ``password`` cannot leak it through this
    record unnoticed.
    """
    checked = validate_kdf_params(params)
    record = {
        "algorithm": checked.algorithm,
        "iterations": str(checked.iterations),
        "salt_length": str(checked.salt_length),
        "dklen": str(checked.dklen),
    }
    return sanitise_audit_entry(record)


def derive_key(
    password: str,
    *,
    params: Any = None,
    salt: Optional[bytes] = None,
    do_audit: bool = True,
) -> DerivedKey:
    """Derive a key using policy-bounded parameters, with an audit record."""
    checked = validate_kdf_params(params)
    if not isinstance(password, str) or not password:
        raise KDFParameterError("password must be a non-empty string")

    if salt is None:
        salt_bytes = os.urandom(checked.salt_length)
    elif not isinstance(salt, (bytes, bytearray)):
        raise KDFParameterError("salt must be bytes")
    else:
        salt_bytes = bytes(salt)
        if not MIN_SALT_LENGTH <= len(salt_bytes) <= MAX_SALT_LENGTH:
            raise KDFParameterError("salt length is outside the allowed range")

    # Keep the UTF-8 bytes in a mutable buffer so the transient copy can be
    # zeroed after use. The caller's immutable str cannot be scrubbed; this is
    # the "where feasible" part of the secret-hygiene contract.
    secret = bytearray(password.encode("utf-8"))
    try:
        key = pbkdf2_raw(
            secret,
            salt_bytes,
            checked.iterations,
            checked.dklen,
            checked.algorithm,
        )
    finally:
        scrub_secret(secret)

    if do_audit:
        audit = audit_kdf_params(checked)
        logger.info(
            "KDF derivation "
            + " ".join(
                f"{name}={audit[name]}"
                for name in ("algorithm", "iterations", "salt_length", "dklen")
            )
        )
    return DerivedKey(key=key, salt=salt_bytes, params=checked)


def verify_derived_key(
    password: str,
    expected_key: bytes,
    *,
    salt: bytes,
    params: Any = None,
) -> bool:
    """Verify a derived key in constant time, failing closed on bad input."""
    if not isinstance(expected_key, (bytes, bytearray)) or not expected_key:
        return False
    try:
        candidate = derive_key(password, params=params, salt=salt, do_audit=False).key
    except KDFParameterError:
        return False
    return constant_time_compare(candidate, bytes(expected_key))


# Known-answer vectors. SHA-256 values are the standard PBKDF2-HMAC-SHA256
# results for ("password", "salt"); the SHA-1 rows are RFC 6070 vectors and
# exercise the raw primitive's other supported hash.
KDF_TEST_VECTORS: tuple[tuple[str, bytes, bytes, int, int, str], ...] = (
    (
        "sha256",
        b"password",
        b"salt",
        1,
        32,
        "120fb6cffcf8b32c43e7225256c4f837a86548c92ccc35480805987cb70be17b",
    ),
    (
        "sha256",
        b"password",
        b"salt",
        2,
        32,
        "ae4d0c95af6b46d32d0adff928f06dd02a303f8ef3c251dfd6e2d85a95474c43",
    ),
    (
        "sha256",
        b"password",
        b"salt",
        4096,
        32,
        "c5e478d59288c841aa530db6845c4c8d962893a001ce4e11a4963873aa98134a",
    ),
    ("sha1", b"password", b"salt", 1, 20, "0c60c80f961f0e71f3a9b524af6012062fe037a6"),
    (
        "sha1",
        b"password",
        b"salt",
        4096,
        20,
        "4b007901b765489abead49d926f721d065a429c1",
    ),
)


def run_kdf_test_vectors() -> bool:
    """Return True when every pinned known-answer vector still matches."""
    return all(
        pbkdf2_raw(password, salt, iterations, dklen, algorithm).hex() == expected
        for algorithm, password, salt, iterations, dklen, expected in KDF_TEST_VECTORS
    )


__all__ = [
    "DEFAULT_PARAMS",
    "MAX_ITERATIONS",
    "MAX_KEY_LENGTH",
    "MAX_SALT_LENGTH",
    "MIN_ITERATIONS",
    "MIN_KEY_LENGTH",
    "MIN_SALT_LENGTH",
    "KDF_TEST_VECTORS",
    "KDFParameterError",
    "KDFParams",
    "DerivedKey",
    "audit_kdf_params",
    "derive_key",
    "pbkdf2_raw",
    "run_kdf_test_vectors",
    "validate_kdf_params",
    "verify_derived_key",
]
