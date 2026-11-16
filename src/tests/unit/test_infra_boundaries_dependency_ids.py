"""Boundary tests for dependency id/param parsing (commit 5)."""

import pytest

from src.api.middleware.dependency_validation import (
    MAX_LIMIT,
    MAX_PAGE,
    bounded_id,
    bounded_limit,
    bounded_page,
    bounded_param_text,
    optional_id,
    parse_param_int,
)
from src.core.security.hardening import HardeningViolation


class TestBoundedId:
    def test_valid(self):
        assert bounded_id("123") == 123
        assert bounded_id(123) == 123

    @pytest.mark.parametrize("bad", [0, -1, "abc", "99000000000000000000", None, 1.5])
    def test_invalid(self, bad):
        with pytest.raises(HardeningViolation):
            bounded_id(bad)

    def test_optional(self):
        assert optional_id(None) is None
        assert optional_id(" ") is None
        assert optional_id("42") == 42
        with pytest.raises(HardeningViolation):
            optional_id("-3")


class TestBoundedPageLimit:
    def test_page(self):
        assert bounded_page("2") == 2
        assert bounded_page(None) == 1
        with pytest.raises(HardeningViolation):
            bounded_page(0)
        with pytest.raises(HardeningViolation):
            bounded_page(MAX_PAGE + 1)

    def test_limit(self):
        assert bounded_limit("25") == 25
        assert bounded_limit(None) == 50
        with pytest.raises(HardeningViolation):
            bounded_limit(0)
        with pytest.raises(HardeningViolation):
            bounded_limit(MAX_LIMIT + 1)

    def test_parse_param_int(self):
        assert parse_param_int("5", "n", minimum=1, maximum=10) == 5
        with pytest.raises(HardeningViolation):
            parse_param_int("11", "n", minimum=1, maximum=10)


class TestBoundedParamText:
    def test_control_characters_rejected(self):
        with pytest.raises(HardeningViolation):
            bounded_param_text("a\r\nb")

    def test_length_bounded(self):
        bounded_param_text("a" * 512)
        with pytest.raises(HardeningViolation):
            bounded_param_text("a" * 513)
