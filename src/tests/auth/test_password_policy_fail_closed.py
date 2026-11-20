"""The common/breached-password list is a control that must fail CLOSED.

If the bundled policy file cannot be read, the check can no longer distinguish
a strong password from a known-compromised one. Validation must reject the
candidate instead of silently skipping the blacklist.
"""

from unittest.mock import patch

from src.core.auth import passwords as pwd_module


def _clear_cache():
    if hasattr(pwd_module.validate_password, "_common_passwords"):
        delattr(pwd_module.validate_password, "_common_passwords")


def test_unreadable_common_password_list_rejects_candidate():
    _clear_cache()
    strong = "X9#vL2!qP7@zR4&kM"
    with (
        patch.object(
            pwd_module, "get_password_config", return_value={"min_length": 12}
        ),
        patch("pathlib.Path.read_text", side_effect=OSError("disk error")),
    ):
        try:
            result = pwd_module.validate_password(strong)
        finally:
            _clear_cache()

    assert result.valid is False
    assert any("policy is temporarily unavailable" in issue for issue in result.issues)


def test_readable_common_password_list_still_flags_common_password():
    """Control: with the file present the blacklist is still enforced."""
    _clear_cache()
    try:
        with patch.object(
            pwd_module,
            "get_password_config",
            return_value={"min_length": 12, "reject_common_passwords": True},
        ):
            result = pwd_module.validate_password("password")
        assert result.valid is False
        assert any("too common" in issue for issue in result.issues)
        assert getattr(pwd_module.validate_password, "_common_passwords", None)
    finally:
        _clear_cache()
