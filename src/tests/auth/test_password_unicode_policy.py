"""Regression tests for Unicode confusable bypasses of the password policy.

A plain ``casefold()`` comparison against an ASCII common-password blacklist
can be evaded with compatibility characters (fullwidth letters, ligatures,
Kelvin sign, ...). NFKC normalization folds those lookalikes back to their
canonical form before the blacklist is consulted.
"""

import unicodedata

from src.core.auth import passwords as pwd_module


POLICY = {
    "min_length": 12,
    "max_length": 128,
    "require_uppercase": True,
    "require_lowercase": True,
    "require_digit": True,
    "require_special": True,
    "reject_common_passwords": True,
}


def _use_policy(monkeypatch, **overrides):
    policy = dict(POLICY, **overrides)
    monkeypatch.setattr(pwd_module, "get_password_config", lambda: policy)


def test_unicode_compatibility_variant_of_common_password_is_rejected(monkeypatch):
    _use_policy(monkeypatch)
    monkeypatch.setattr(
        pwd_module.validate_password,
        "_common_passwords",
        pwd_module._password_policy_forms("Password123!"),
        raising=False,
    )

    # Sanity: the plain form is blacklisted.
    plain = pwd_module.validate_password("Password123!")
    assert plain.valid is False
    assert any("too common" in issue for issue in plain.issues)

    # U+FF41 FULLWIDTH LATIN SMALL LETTER A is accepted by every composition
    # rule (the remaining ASCII characters supply upper/lower/digit/special),
    # but casefold() alone leaves it distinct from the blacklist entry. NFKC
    # maps the variant straight onto "Password123!".
    variant = "P\uff41ssword123!"
    assert unicodedata.normalize("NFKC", variant).casefold() == "password123!"

    result = pwd_module.validate_password(variant)
    assert result.valid is False
    assert any("too common" in issue for issue in result.issues)


def test_blacklist_entries_are_normalized_too(monkeypatch):
    """A blacklist entry stored with compatibility chars still matches ASCII."""
    _use_policy(monkeypatch)
    monkeypatch.setattr(
        pwd_module.validate_password,
        "_common_passwords",
        pwd_module._password_policy_forms("P\uff41ssword123!"),
        raising=False,
    )
    result = pwd_module.validate_password("Password123!")
    assert result.valid is False
    assert any("too common" in issue for issue in result.issues)


def test_strong_non_common_password_remains_valid(monkeypatch):
    _use_policy(monkeypatch)
    monkeypatch.setattr(
        pwd_module.validate_password,
        "_common_passwords",
        pwd_module._password_policy_forms("Password123!"),
        raising=False,
    )
    result = pwd_module.validate_password("Caf\u00e9Pass123!")
    assert result.valid is True
    assert result.issues == []


def test_absolute_length_ceiling_wins_over_misconfigured_max_length(monkeypatch):
    """An unbounded password cannot be accepted even if config says otherwise."""
    _use_policy(monkeypatch, max_length=100_000, reject_common_passwords=False)
    result = pwd_module.validate_password("Aa1!" + "x" * 2000)
    assert result.valid is False
    assert any("at most 1024" in issue for issue in result.issues)
