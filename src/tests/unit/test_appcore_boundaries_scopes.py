"""Caps and closed-set validation for OAuth scopes and permission strings."""

import pytest

from src.core.applications.oauth.scopes import (
    MAX_SCOPES_PER_REQUEST,
    MAX_SCOPE_LENGTH,
    MAX_PERMISSION_LENGTH,
    require_valid_scopes,
    validate_permission_string,
    validate_scopes,
)
from src.core.applications.commands.validation import validate_command
from src.core.applications.oauth.flows import OAuth2Flow
from src.core.applications.exceptions import InvalidScopeError
from src.core.security.hardening import HardeningViolation


class TestScopeValidation:
    def test_accepts_and_deduplicates_known_scopes(self):
        assert require_valid_scopes(["identify", "email", "identify"]) == [
            "identify",
            "email",
        ]

    def test_rejects_empty_unknown_and_oversized_lists(self):
        for value in (
            None,
            [],
            ["not_a_scope"],
            ["identify"] * (MAX_SCOPES_PER_REQUEST + 1),
        ):
            with pytest.raises(HardeningViolation):
                require_valid_scopes(value)

    @pytest.mark.parametrize(
        "scope",
        ["IDENTIFY", "", "a" * (MAX_SCOPE_LENGTH + 1), "bad scope", "id\nentify"],
    )
    def test_rejects_malformed_scope_strings(self, scope):
        with pytest.raises(HardeningViolation):
            require_valid_scopes([scope])

    def test_non_iterable(self):
        with pytest.raises(HardeningViolation):
            require_valid_scopes(42)

    def test_validate_scopes_reports_caps(self):
        assert validate_scopes(["identify"])[0] is True
        assert validate_scopes([])[0] is False
        assert validate_scopes(["identify"] * (MAX_SCOPES_PER_REQUEST + 1))[0] is False
        assert validate_scopes(["a" * (MAX_SCOPE_LENGTH + 1)])[0] is False


class TestPermissionStrings:
    def test_canonical_and_default(self):
        assert validate_permission_string(None) == "0"
        assert validate_permission_string("  8  ") == "8"
        assert validate_permission_string(2**64 - 1) == str(2**64 - 1)

    @pytest.mark.parametrize(
        "value",
        ["", "abc", "-1", "1.5", "0x10", "a" * (MAX_PERMISSION_LENGTH + 1), 2**64],
    )
    def test_rejects_bad_values(self, value):
        with pytest.raises(HardeningViolation):
            validate_permission_string(value)

    def test_command_validation_uses_permission_bound(self):
        ok, issues = validate_command(
            {
                "name": "test",
                "description": "desc",
                "default_member_permissions": "8",
            }
        )
        assert ok, issues
        ok, issues = validate_command(
            {
                "name": "test",
                "description": "desc",
                "default_member_permissions": "a" * (MAX_PERMISSION_LENGTH + 1),
            }
        )
        assert not ok and issues


class TestOAuthFlowBoundary:
    def _flow(self, app_manager):
        return OAuth2Flow(
            app_manager._db,
            {"code_expiry_seconds": 600, "token_expiry_seconds": 604800},
        )

    def test_generate_url_rejects_oversized_scopes_and_permissions(
        self, app_manager, test_user
    ):
        app = app_manager.create_application(owner_id=test_user.id, name="Scope App")
        flow = self._flow(app_manager)
        bad_scopes = ["identify"] * (MAX_SCOPES_PER_REQUEST + 1)
        with pytest.raises(InvalidScopeError):
            flow.generate_authorization_url(
                app.id, "https://example.com/cb", bad_scopes
            )
        with pytest.raises(InvalidScopeError):
            flow.generate_authorization_url(
                app.id,
                "https://example.com/cb",
                ["bot"],
                permissions="not-a-bitfield",
            )

    def test_authorization_request_and_code_reject_bad_scopes(
        self, app_manager, test_user
    ):
        app = app_manager.create_application(
            owner_id=test_user.id,
            name="Strict Scope App",
            redirect_uris=["https://example.com/cb"],
        )
        flow = self._flow(app_manager)
        bad_scopes = ["unknown_scope"]
        with pytest.raises(InvalidScopeError):
            flow.validate_authorization_request(
                app.id, "https://example.com/cb", bad_scopes
            )
        with pytest.raises(InvalidScopeError):
            flow.create_authorization_code(
                app.id, test_user.id, "https://example.com/cb", bad_scopes
            )
