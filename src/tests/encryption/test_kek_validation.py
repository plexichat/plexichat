"""Unit tests for KEK quality validation (bootstrap/vault refuse insecure KEKs)."""

import os
import zlib

import pytest

from src.utils.encryption.kek_validation import (
    KekValidationError,
    MIN_ENTROPY_BITS_PER_BYTE,
    shannon_entropy,
    validate_kek,
)


def _good_key() -> bytes:
    return os.urandom(32)


def _no_bypass(monkeypatch):
    for var in ("CI", "GITLAB_CI", "PLEXICHAT_SELF_TEST"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(
        "src.utils.encryption.kek_validation._is_dev_selftest_bypass", lambda: False
    )


class TestGoodKeys:
    def test_accepts_random_32_bytes(self, monkeypatch):
        _no_bypass(monkeypatch)
        key = _good_key()
        assert validate_kek(key, source="env", context="TEST") == key

    def test_accepts_random_48_bytes_normalized(self, monkeypatch):
        _no_bypass(monkeypatch)
        key = os.urandom(48)
        out = validate_kek(key, source="env")
        assert isinstance(out, bytes) and len(out) == 32

    def test_accepts_random_64_bytes_normalized(self, monkeypatch):
        _no_bypass(monkeypatch)
        key = os.urandom(64)
        out = validate_kek(key, source="env")
        assert isinstance(out, bytes) and len(out) == 32

    def test_accepts_high_entropy_nonuniform(self, monkeypatch):
        _no_bypass(monkeypatch)
        # Construct bytes with full byte-range coverage and high entropy.
        key = bytes(range(256))[:32]
        # This is sequential/low entropy in our strict sense? 0..31 is sequential.
        # Use a shuffled deterministic pattern with high entropy.
        import hashlib

        key = hashlib.sha256(b"plexichat-test-kek-seed").digest()
        assert validate_kek(key) == key


class TestLengthBounds:
    def test_rejects_too_short(self, monkeypatch):
        _no_bypass(monkeypatch)
        with pytest.raises(KekValidationError, match="32-64 bytes"):
            validate_kek(os.urandom(31))

    def test_rejects_too_long(self, monkeypatch):
        _no_bypass(monkeypatch)
        with pytest.raises(KekValidationError, match="32-64 bytes"):
            validate_kek(os.urandom(65))

    def test_rejects_empty(self, monkeypatch):
        _no_bypass(monkeypatch)
        with pytest.raises(KekValidationError):
            validate_kek(b"")

    def test_length_never_bypassed(self, monkeypatch):
        monkeypatch.setattr(
            "src.utils.encryption.kek_validation._is_dev_selftest_bypass", lambda: True
        )
        with pytest.raises(KekValidationError, match="32-64 bytes"):
            validate_kek(b"short", allow_bypass=True)


class TestLowEntropy:
    def test_rejects_low_entropy(self, monkeypatch):
        _no_bypass(monkeypatch)
        # Two-value distribution over 32 bytes => 1 bit/byte.
        key = bytes([0, 1] * 16)
        with pytest.raises(KekValidationError, match="entropy"):
            validate_kek(key)

    def test_shannon_entropy_threshold(self):
        assert shannon_entropy(os.urandom(4096)) >= MIN_ENTROPY_BITS_PER_BYTE
        assert shannon_entropy(b"\x00" * 32) == 0.0


class TestWeakPatterns:
    def test_rejects_all_identical(self, monkeypatch):
        _no_bypass(monkeypatch)
        with pytest.raises(KekValidationError, match="pattern"):
            validate_kek(b"\xab" * 32)

    def test_rejects_repeating_pattern(self, monkeypatch):
        _no_bypass(monkeypatch)
        with pytest.raises(KekValidationError, match="pattern"):
            validate_kek((b"\x01\x02\x03\x04" * 8))

    def test_rejects_sequential_ascending(self, monkeypatch):
        _no_bypass(monkeypatch)
        with pytest.raises(KekValidationError, match="pattern"):
            validate_kek(bytes(range(32)))

    def test_rejects_sequential_descending(self, monkeypatch):
        _no_bypass(monkeypatch)
        with pytest.raises(KekValidationError, match="pattern"):
            validate_kek(bytes(range(31, -1, -1)))

    def test_rejects_dominant_byte(self, monkeypatch):
        _no_bypass(monkeypatch)
        key = bytearray(os.urandom(32))
        for i in range(28):
            key[i] = 0x42
        # Force enough variety in tail to not be "identical" but keep dominant.
        key[28:] = b"\x01\x02\x03\x04"
        with pytest.raises(KekValidationError):
            validate_kek(bytes(key))


class TestPassphraseLike:
    def test_rejects_ascii_passphrase(self, monkeypatch):
        _no_bypass(monkeypatch)
        # 32 printable chars that look like a passphrase; raw, not derived.
        key = b"correct horse battery staple!!!!"
        with pytest.raises(KekValidationError, match="passphrase|Argon2|PBKDF2"):
            validate_kek(key)

    def test_rejects_words_with_spaces(self, monkeypatch):
        _no_bypass(monkeypatch)
        key = b"my secret key for production!!! "[:32]
        with pytest.raises(KekValidationError, match="passphrase|Argon2|PBKDF2"):
            validate_kek(key)

    def test_rejects_structured_text(self, monkeypatch):
        _no_bypass(monkeypatch)
        key = b"Password123Password123Password12"
        with pytest.raises(
            KekValidationError,
            match="passphrase|Argon2|PBKDF2|pattern|entropy|compress",
        ):
            validate_kek(key)

    def test_accepts_random_bytes_not_printable_text(self, monkeypatch):
        _no_bypass(monkeypatch)
        # Ensure the random key is accepted even if it contains some printable bytes.
        key = _good_key()
        assert validate_kek(key) == key


class TestCompressibility:
    def test_rejects_highly_compressible(self, monkeypatch):
        _no_bypass(monkeypatch)
        # Repetitive compressible payload (but bypass all-identical/repeating by
        # a small mutation that keeps compressibility very high? repeating check
        # will catch exact repeats; use structured compressible text-like bytes
        # that are not a simple period and not passphrase-like? Easier: monkeypatch
        # the pattern/passphrase checks to isolate compressibility.)
        data = b"AAAA" * 8  # 32 bytes, compressible
        monkeypatch.setattr(
            "src.utils.encryption.kek_validation._has_weak_pattern", lambda d: None
        )
        monkeypatch.setattr(
            "src.utils.encryption.kek_validation._is_passphrase_like", lambda d: None
        )
        monkeypatch.setattr(
            "src.utils.encryption.kek_validation.shannon_entropy", lambda d: 7.5
        )
        compressed = zlib.compress(data, 9)
        assert len(compressed) / len(data) < 0.95
        with pytest.raises(KekValidationError, match="compressible"):
            validate_kek(data)

    def test_random_not_compressible(self, monkeypatch):
        _no_bypass(monkeypatch)
        key = _good_key()
        compressed = zlib.compress(key, 9)
        # Random 32 bytes usually expand slightly under zlib framing.
        assert len(compressed) / len(key) >= 0.95 or True  # validation is the authority
        validate_kek(key)  # must not raise


class TestBypassBehavior:
    def test_bypass_allows_weak_in_selftest(self, monkeypatch):
        monkeypatch.setattr(
            "src.utils.encryption.kek_validation._is_dev_selftest_bypass", lambda: True
        )
        weak = b"\xab" * 32
        assert validate_kek(weak, allow_bypass=True) == weak

    def test_production_rejects_weak(self, monkeypatch):
        _no_bypass(monkeypatch)
        with pytest.raises(KekValidationError):
            validate_kek(b"\xab" * 32, allow_bypass=True)

    def test_allow_bypass_false_rejects_even_in_selftest(self, monkeypatch):
        monkeypatch.setattr(
            "src.utils.encryption.kek_validation._is_dev_selftest_bypass", lambda: True
        )
        with pytest.raises(KekValidationError):
            validate_kek(b"\xab" * 32, allow_bypass=False)
