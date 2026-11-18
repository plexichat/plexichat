"""Regression tests for PlexiJoin token/timestamp validation hardening.

1. ``validate_token`` used ``re.match`` with a ``$`` anchor. In Python ``$``
   also matches just before a trailing newline, so nonces, shared keys and
   join codes ending in ``"\\n"`` were accepted even though every other field
   validator rejects control characters. Those values then flowed into HMAC
   signature material, replay-nonce keys and HTTP headers.
2. ``validate_timestamp`` relied on ``str.isdigit()``, which is True for
   non-ASCII digits that ``int()`` cannot parse, leaking a bare ``ValueError``
   instead of the ``HardeningViolation`` every caller handles.
"""

from __future__ import annotations

import pytest

from src.core.plexijoin.manager import PlexiJoinManager
from src.core.plexijoin.validation import (
    validate_join_code,
    validate_join_envelope,
    validate_nonce,
    validate_shared_key,
    validate_timestamp,
    validate_token,
)
from src.core.security.hardening import HardeningViolation

pytestmark = pytest.mark.unit

_NOW = 1_700_000_000


def _token(value):
    """validate_token takes an explicit field name; pin it for parametrize."""
    return validate_token(value, "token")


@pytest.mark.parametrize(
    "validator,value",
    [
        (_token, "abc\n"),
        (validate_nonce, "abc\n"),
        (validate_nonce, "nonceABC123\n"),
        (validate_shared_key, "sekret\n"),
        (validate_join_code, "Abc123_-\n"),
        (validate_join_code, "Abc123_-\r\n"),
    ],
)
def test_tokens_with_trailing_newline_are_rejected(validator, value):
    with pytest.raises(HardeningViolation):
        validator(value)


@pytest.mark.parametrize(
    "validator,value",
    [
        (_token, "abcDEF0123_-"),
        (validate_nonce, "nonceABC123"),
        (validate_shared_key, "sekret"),
        (validate_join_code, "Abc123_-"),
    ],
)
def test_well_formed_tokens_still_accepted(validator, value):
    assert validator(value) == value


def test_join_envelope_rejects_newline_nonce():
    envelope = {
        "instance_id": "remote-a",
        "join_code": "Abc123_-",
        "nonce": "nonceABC123\n",
        "timestamp": _NOW,
    }
    with pytest.raises(HardeningViolation):
        validate_join_envelope(envelope)


def test_newline_nonce_never_reaches_replay_store():
    class _DB:
        def __getattr__(self, name):
            raise AssertionError(f"database must not be touched: {name}")

    manager = PlexiJoinManager(_DB())
    assert manager.consume_replay_nonce(1, "abc\n", _NOW) is False


@pytest.mark.parametrize(
    "bad",
    [
        "\u00b2",  # superscript two: isdigit() True, int() raises ValueError
        "\u0661\u0667\u0660\u0660",  # Arabic-Indic digits: int() parses them
        "9" * 5000,  # CPython int() digit limit raises ValueError
    ],
)
def test_timestamp_rejects_non_ascii_or_oversized_digits(bad):
    with pytest.raises(HardeningViolation):
        validate_timestamp(bad)


def test_ascii_timestamp_still_accepted():
    assert validate_timestamp(str(_NOW)) == _NOW
    assert validate_timestamp(_NOW) == _NOW
