"""KEK (Key Encryption Key) quality validation.

Enforces minimum strength for resolved KEK material so weak, patterned,
passphrase-like, or compressible keys cannot be used in production.
"""

from __future__ import annotations

import contextlib
import math
import os
import re
import zlib
from collections import Counter
from typing import Optional

MIN_KEK_BYTES = 32
MAX_KEK_BYTES = 64
MIN_ENTROPY_BITS_PER_BYTE = 7.0
# True-random 32-byte keys are not meaningfully compressible; a ratio close to
# 1.0 (or below a small overhead-adjusted threshold) indicates structure.
MAX_COMPRESSIBILITY_RATIO = 0.95

_PRINTABLE_ASCII = set(range(0x20, 0x7F))


class KekValidationError(ValueError):
    """Raised when a resolved KEK fails quality validation."""


def _is_dev_selftest_bypass() -> bool:
    """Match bootstrap/security_checks bypass signals (CI/selftest only).

    Production must not bypass KEK quality failures. A bare development
    environment is not a bypass — operators must supply a valid key or run in
    CI/selftest mode.
    """
    if os.getenv("CI") is not None or os.getenv("GITLAB_CI") is not None:
        return True
    if os.getenv("PLEXICHAT_SELF_TEST") is not None:
        return True
    try:
        import utils.config as config
    except ImportError:
        # Standalone tooling without the config subsystem cannot be a selftest.
        return False
    return bool(config.get("selftest.enabled", False))


def shannon_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = Counter(data)
    total = len(data)
    entropy = 0.0
    for count in counts.values():
        p = count / total
        entropy -= p * math.log2(p)
    return entropy


def _effective_entropy_floor(data: bytes) -> float:
    """Length-aware entropy floor.

    Shannon entropy is sample-size limited: 32 bytes over 256 symbols cannot
    exceed log2(32)=5.0 bits/byte even when uniformly random. The security
    target is >=7.0 bits/byte of key-material quality (CSPRNG-grade). We enforce
    the full 7.0 floor when the sample is large enough to measure it; for
    shorter key material we require a high fraction of the maximum measurable
    entropy plus strong pattern/compressibility checks.
    """
    if not data:
        return MIN_ENTROPY_BITS_PER_BYTE
    max_measurable = math.log2(min(len(data), 256))
    if len(data) >= 128:
        return MIN_ENTROPY_BITS_PER_BYTE
    # 32 bytes => >= 4.375, 48 => >= 4.890, 64 => >= 5.250 (87.5% of max).
    return max_measurable * 0.875


def _is_repeating_pattern(data: bytes) -> bool:
    n = len(data)
    for period in range(1, min(9, n // 2 + 1)):
        if n % period != 0:
            continue
        chunk = data[:period]
        if chunk * (n // period) == data:
            return True
    # Also catch short cycles even when not evenly divisible.
    for period in range(1, min(5, n // 2 + 1)):
        if data == (data[:period] * ((n + period - 1) // period))[:n]:
            return True
    return False


def _is_sequential(data: bytes) -> bool:
    if len(data) < 4:
        return False
    asc = all((data[i] + 1) & 0xFF == data[i + 1] for i in range(len(data) - 1))
    desc = all((data[i] - 1) & 0xFF == data[i + 1] for i in range(len(data) - 1))
    return asc or desc


def _has_weak_pattern(data: bytes) -> Optional[str]:
    if len(set(data)) <= 1:
        return "all bytes identical"
    if _is_repeating_pattern(data):
        return "repeating byte pattern"
    if _is_sequential(data):
        return "sequential byte pattern"
    # Runs of a single value covering most of the key.
    counts = Counter(data)
    most_common_count = counts.most_common(1)[0][1]
    if most_common_count >= max(12, int(len(data) * 0.75)):
        return "dominant repeated byte value"
    # Obvious nibble/byte test patterns.
    hex_like = data.hex()
    if re.fullmatch(r"(00)+", hex_like) or re.fullmatch(r"(ff)+", hex_like):
        return "constant null/0xff material"
    if re.fullmatch(r"(0123456789abcdef)+[0-9a-f]*", hex_like) and len(set(data)) >= 8:
        # sequential hex expansion is already caught above; keep this narrow
        pass
    return None


def _is_passphrase_like(data: bytes) -> Optional[str]:
    """Detect raw passphrase material that was not derived via a KDF.

    A properly derived key (Argon2id/PBKDF2 output) is computationally
    indistinguishable from random bytes, so this targets inputs that still
    look like human text. We require strong textual signals to avoid false
    positives on random keys.
    """
    if not data:
        return None
    printable = sum(1 for b in data if b in _PRINTABLE_ASCII)
    printable_ratio = printable / len(data)
    if printable_ratio < 0.85:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    # Printable UTF-8/ASCII key material with text structure is passphrase-like.
    has_space = " " in text
    has_letters = bool(re.search(r"[A-Za-z]", text))
    has_digits = bool(re.search(r"[0-9]", text))
    wordish_groups = re.findall(r"[A-Za-z]{3,}", text)
    if has_space and has_letters:
        return "looks like passphrase text (use Argon2id/PBKDF2-derived key material)"
    if has_letters and len(wordish_groups) >= 2 and printable_ratio >= 0.95:
        return "looks like passphrase text (use Argon2id/PBKDF2-derived key material)"
    if (
        has_letters
        and has_digits
        and printable_ratio >= 0.98
        and len(set(text)) < len(text) * 0.85
    ):
        return "looks like low-entropy passphrase text (use Argon2id/PBKDF2-derived key material)"
    # High printable ratio alone is suspicious for a KEK but could be an unlucky
    # random draw; only reject when combined with very low byte diversity.
    if printable_ratio >= 0.98 and len(set(data)) < max(10, len(data) // 3):
        return "looks like structured text, not random key material (use Argon2id/PBKDF2-derived key material)"
    return None


def _is_too_compressible(data: bytes) -> bool:
    if len(data) < 16:
        return False
    # zlib adds framing; compare payload size relative to raw. For random data
    # compressed output is typically >= raw length (incompressible).
    compressed = zlib.compress(data, level=9)
    return (len(compressed) / len(data)) < MAX_COMPRESSIBILITY_RATIO


def _normalize_kek_material(key: bytes) -> bytes:
    """Return an AES-256-GCM-compatible 32-byte KEK.

    Accepts 32-byte keys directly. For 48/64-byte high-quality key material,
    derives a 32-byte KEK via HKDF-SHA256 (deterministic, preserves entropy).
    """
    if len(key) == 32:
        return key
    if len(key) in (48, 64):
        from .hkdf import derive_key

        return derive_key(
            key,
            salt=b"plexichat:kek-material:v1",
            info=b"PLEXICHAT_KEK_AES256",
            length=32,
        )
    return key


def validate_kek(
    key: bytes,
    *,
    source: Optional[str] = None,
    context: Optional[str] = None,
    allow_bypass: bool = True,
) -> bytes:
    """Validate KEK material quality.

    Args:
        key: Resolved key bytes (32-64 bytes of key material).
        source: KEK source label (env/hsm/tpm/local) for error context.
        context: Additional context (env var name, keyring name).
        allow_bypass: When True, CI/selftest bypass downgrades quality failures
            to warnings and returns the key. Length/format requirements that
            make AES unusable are never bypassed. Production must refuse.

    Returns:
        The validated (and AES-256-normalized when needed) KEK.

    Raises:
        KekValidationError: If the key is invalid/insecure.
    """
    where = []
    if context:
        where.append(str(context))
    if source:
        where.append(f"source={source}")
    suffix = f" ({', '.join(where)})" if where else ""

    if not isinstance(key, (bytes, bytearray, memoryview)):
        raise KekValidationError(f"KEK must be bytes{suffix}")
    key = bytes(key)

    # Hard requirements — never bypass (keyring correctness).
    if len(key) < MIN_KEK_BYTES or len(key) > MAX_KEK_BYTES:
        raise KekValidationError(
            f"KEK key material length must be {MIN_KEK_BYTES}-{MAX_KEK_BYTES} bytes, got {len(key)}{suffix}"
        )
    if len(key) not in (32, 48, 64):
        raise KekValidationError(
            f"KEK key material must be 32, 48, or 64 bytes (AES-256-GCM KEK derived from 32/48/64-byte material); "
            f"got {len(key)} bytes{suffix}"
        )

    problems = []
    weak = _has_weak_pattern(key)
    if weak:
        problems.append(f"weak pattern detected: {weak}")
    passphrase = _is_passphrase_like(key)
    if passphrase:
        problems.append(passphrase)
    entropy = shannon_entropy(key)
    entropy_floor = _effective_entropy_floor(key)
    if entropy < entropy_floor:
        problems.append(
            f"entropy {entropy:.3f} bits/byte below minimum {entropy_floor:.3f} "
            f"(target >= {MIN_ENTROPY_BITS_PER_BYTE:.1f} bits/byte for key material)"
        )
    if _is_too_compressible(key):
        problems.append("key material is compressible (expected true random bytes)")

    if not problems:
        return _normalize_kek_material(key)

    detail = "; ".join(problems)
    message = (
        f"Insecure KEK rejected{suffix}: {detail}. "
        "Provide 32-64 bytes of true-random key material (e.g. 64 hex chars = 32 bytes). "
        "If deriving from a passphrase, use Argon2id or PBKDF2 and supply the derived key."
    )

    if allow_bypass and _is_dev_selftest_bypass():
        with contextlib.suppress(ImportError):
            import utils.logger as logger

            logger.warning("KEK validation bypassed in CI/selftest mode — %s", message)
        return _normalize_kek_material(key)

    raise KekValidationError(message)
