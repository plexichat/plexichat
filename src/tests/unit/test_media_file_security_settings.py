"""Fail-closed media security settings.

The point of these tests is that a *typo* must be loud. Before, every key was
read with ``.get`` into a plain dict, so ``magic_byte_validaton`` (note the
missing ``i``) silently meant "not configured" and the permissive default won.
"""

from dataclasses import FrozenInstanceError

import pytest

from src.core.media.security.settings import ConfigTypeError, FileSecuritySettings

DEFAULTS = {
    "default_extensions": {".exe", ".bat"},
    "default_mime_types": {"application/x-msdownload"},
}


def test_empty_config_takes_the_explicit_defaults():
    settings = FileSecuritySettings.from_mapping({}, **DEFAULTS)
    assert settings.magic_byte_validation is True
    assert settings.block_executables is True
    assert settings.blocked_extensions == frozenset({".exe", ".bat"})
    assert settings.blocked_mime_types == frozenset({"application/x-msdownload"})


def test_none_config_is_treated_as_empty():
    assert FileSecuritySettings.from_mapping(None, **DEFAULTS) == (
        FileSecuritySettings.from_mapping({}, **DEFAULTS)
    )


def test_configured_values_are_read():
    settings = FileSecuritySettings.from_mapping(
        {
            "magic_byte_validation": False,
            "block_executables": False,
            "blocked_extensions": [".scr"],
            "blocked_mime_types": ["application/x-sh"],
        },
        **DEFAULTS,
    )
    assert settings.magic_byte_validation is False
    assert settings.block_executables is False
    assert settings.blocked_extensions == frozenset({".scr"})
    assert settings.blocked_mime_types == frozenset({"application/x-sh"})


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None])
def test_non_boolean_switches_raise_instead_of_coercing(value):
    with pytest.raises(ConfigTypeError):
        FileSecuritySettings.from_mapping({"magic_byte_validation": value}, **DEFAULTS)


def test_a_bare_string_is_not_a_collection_of_extensions():
    # "$.exe" would otherwise become three "extensions" and block nothing.
    with pytest.raises(ConfigTypeError):
        FileSecuritySettings.from_mapping({"blocked_extensions": ".exe"}, **DEFAULTS)


def test_non_string_members_are_rejected():
    with pytest.raises(ConfigTypeError):
        FileSecuritySettings.from_mapping(
            {"blocked_mime_types": ["application/x-sh", 7]}, **DEFAULTS
        )


def test_settings_are_immutable():
    settings = FileSecuritySettings.from_mapping({}, **DEFAULTS)
    with pytest.raises(FrozenInstanceError):
        # setattr rather than attribute assignment so the immutability check does
        # not depend on a type-checker suppression.
        setattr(settings, "magic_byte_validation", False)
