"""Join payload caps and invite-code validation."""

import pytest

from src.api.routes.join import (
    INVITE_CODE_PATTERN,
    _bounded_text,
    validate_invite_code,
    validate_join_payload,
)
from src.core.security.hardening import HardeningViolation


@pytest.mark.parametrize("code", ["abc123", "ABCD-efgh_01", "x" * 64])
def test_valid_invite_codes(code):
    assert INVITE_CODE_PATTERN.match(code)
    assert validate_invite_code(code) == code


@pytest.mark.parametrize(
    "code", ["", "has space", "bad/slash", "x" * 65, "ctrl\x00code", 12345]
)
def test_invalid_invite_codes(code):
    with pytest.raises(HardeningViolation):
        validate_invite_code(code)


def test_join_payload_validates_nested_invite_codes():
    validate_join_payload({"a": {"b": ["ok", {"invite_code": "abc123"}]}})
    with pytest.raises(HardeningViolation):
        validate_join_payload({"a": {"invite_code": "bad code"}})


def test_join_payload_depth_and_width():
    nested = 0
    for _ in range(12):
        nested = {"a": nested}
    with pytest.raises(HardeningViolation):
        validate_join_payload(nested)
    with pytest.raises(HardeningViolation):
        validate_join_payload({str(i): i for i in range(200)})


def test_bounded_text_rejects_controls_and_oversize():
    assert _bounded_text("note", "note", 10) == "note"
    with pytest.raises(HardeningViolation):
        _bounded_text("x\x00y", "note", 10)
    with pytest.raises(HardeningViolation):
        _bounded_text("x" * 20, "note", 10)
