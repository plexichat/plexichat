"""PlexiJoin join tokens, codes and nonces must have a safe format."""

import pytest

from src.core.plexijoin.manager import PlexiJoinManager
from src.core.plexijoin.validation import (
    MAX_JOIN_CODE_LENGTH,
    MAX_TOKEN_LENGTH,
    validate_join_code,
    validate_join_envelope,
    validate_nonce,
    validate_shared_key,
    validate_timestamp,
    validate_token,
)
from src.core.security.hardening import HardeningViolation

_NOW = 1_700_000_000


class TestTokenFormat:
    def test_valid_tokens(self):
        assert validate_nonce("abcDEF0123_-") == "abcDEF0123_-"
        assert validate_token("x" * MAX_TOKEN_LENGTH, "token")

    @pytest.mark.parametrize(
        "bad",
        ["", None, 123, "has space", "a\r\nb", "a.b/c", "x" * (MAX_TOKEN_LENGTH + 1)],
    )
    def test_invalid_tokens_rejected(self, bad):
        with pytest.raises(HardeningViolation):
            validate_nonce(bad)

    def test_shared_key_shape(self):
        assert validate_shared_key("sekret") == "sekret"
        assert len(validate_shared_key(PlexiJoinManager.generate_shared_key())) >= 32
        with pytest.raises(HardeningViolation):
            validate_shared_key("short", require_strong=True)
        with pytest.raises(HardeningViolation):
            validate_shared_key("a" * (MAX_TOKEN_LENGTH + 1))


class TestJoinCodes:
    def test_valid_code(self):
        assert validate_join_code("Abc123_-") == "Abc123_-"

    @pytest.mark.parametrize(
        "bad",
        ["short", "", None, "bad code", "x" * (MAX_JOIN_CODE_LENGTH + 1), 123],
    )
    def test_invalid_code_rejected(self, bad):
        with pytest.raises(HardeningViolation):
            validate_join_code(bad)


class TestTimestamps:
    def test_valid(self):
        assert validate_timestamp(str(_NOW)) == _NOW
        assert validate_timestamp(_NOW) == _NOW

    @pytest.mark.parametrize("bad", [-1, 0.5, "1e9", "not-a-number", 4_102_444_801])
    def test_invalid(self, bad):
        with pytest.raises(HardeningViolation):
            validate_timestamp(bad)


class TestJoinEnvelope:
    def test_valid_envelope(self):
        envelope = {
            "instance_id": "remote-a",
            "join_code": "Abc123_-",
            "nonce": "nonceABC123",
            "timestamp": _NOW,
        }
        assert validate_join_envelope(envelope)["nonce"] == "nonceABC123"

    def test_bad_code_rejected(self):
        envelope = {
            "instance_id": "remote-a",
            "join_code": "bad code!",
            "nonce": "nonceABC123",
            "timestamp": _NOW,
        }
        with pytest.raises(HardeningViolation):
            validate_join_envelope(envelope)


class TestReplayNonceManager:
    def test_bad_nonce_never_touches_db(self):
        class _DB:
            def __getattr__(self, _name):
                raise AssertionError("database must not be touched")

        manager = PlexiJoinManager(_DB())
        assert manager.consume_replay_nonce(1, "bad nonce!", _NOW) is False
        assert manager.consume_replay_nonce(1, "ok_nonce", "not-a-time") is False
        assert manager.consume_replay_nonce(1, "ok_nonce", _NOW - 10_000) is False
