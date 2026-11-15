"""The shared text policies: behaviour, message and registry.

These are the unit-level guarantees the per-route call sites rely on, so a
change to the character set or the error message is visible here rather than in
whichever route happens to run first in CI.
"""

import pytest

from src.core.security.errors import TextRejected, TextTooLong
from src.core.security.policies import (
    TEXT_POLICIES,
    Composite,
    MaxLength,
    RejectUICharacters,
    check_text,
    is_control_character,
    text_policy,
)


@pytest.mark.parametrize("character", ["<", ">", '"', "'", "&", "="])
def test_metacharacters_are_rejected(character):
    with pytest.raises(TextRejected) as rejection:
        RejectUICharacters().check(f"channel{character}name", "channel name")
    # The message names the field: a 400 that says only "invalid text" leaves
    # the user guessing which input the form rejected.
    assert "channel name" in str(rejection.value)
    assert "HTML metacharacters" in str(rejection.value)


@pytest.mark.parametrize("character", ["\x00", "\x1f", "\x7f"])
def test_control_characters_are_rejected(character):
    with pytest.raises(TextRejected):
        RejectUICharacters().check(f"name{character}", "name")


def test_ordinary_text_passes_untouched():
    value = "General — 日本語 🎉 (off-topic, 24/7)"
    assert RejectUICharacters().check(value, "name") == value


def test_empty_text_is_allowed():
    # Optional fields arrive as "" from a form; rejecting that would turn a
    # cleared field into a 422.
    assert RejectUICharacters().check("", "name") == ""


def test_is_control_character():
    assert is_control_character("\x00")
    assert is_control_character("\x1b")
    assert is_control_character("\x7f")
    assert not is_control_character("A")
    assert not is_control_character(" ")


def test_max_length_rejects_long_text():
    assert MaxLength(3).check("abc") == "abc"
    with pytest.raises(TextTooLong) as rejection:
        MaxLength(3).check("abcd", "nickname")
    assert "nickname" in str(rejection.value)


def test_composite_reports_the_first_failing_policy():
    policy = Composite((RejectUICharacters(), MaxLength(5)), name="ui_text_5")
    with pytest.raises(TextRejected):
        policy.check("a<b", "field")
    with pytest.raises(TextTooLong):
        policy.check("abcdef", "field")
    assert policy.check("abc", "field") == "abc"


def test_registry_is_usable_by_name():
    assert text_policy("ui_text") is TEXT_POLICIES["ui_text"]
    assert check_text("ui_text_200", "ok") == "ok"
    with pytest.raises(ValueError):
        text_policy("not_a_policy")


def test_registry_entries_all_enforce_something():
    # A registered name that accepts metacharacters would be a policy in name
    # only; the schema coverage test would then bless an unprotected field.
    for name, policy in TEXT_POLICIES.items():
        with pytest.raises(TextRejected):
            policy.check("bad<name", name)


def test_rejection_is_a_value_error_with_client_safe_detail():
    rejection = TextRejected("host 10.0.0.5 refused", field="name")
    # Call sites wrap validation in ``except ValueError``; that must keep
    # working after the taxonomy was introduced.
    assert isinstance(rejection, ValueError)
    detail = rejection.public_detail
    assert detail["code"] == "text_rejected"
    assert detail["field"] == "name"
    assert "10.0.0.5" not in detail["message"]
