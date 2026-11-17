"""Regression tests for request-schema input bounds.

The HTTP body limit (10 MB by default) is not a substitute for field bounds:
a valid JSON body can still carry a password large enough to make a KDF call
expensive, or an array large enough to blow up a storage write. These tests
pin the bounds added to the auth/user/admin request schemas.
"""

import pytest
from pydantic import ValidationError

from src.api.schemas.admin.admin_auth import (
    AdminChangePasswordRequest,
    AdminLoginRequest,
    AdminOTPDisableRequest,
    AdminOTPSetupBeginRequest,
)
from src.api.schemas.auth import (
    MAX_PASSWORD_INPUT_LENGTH,
    LoginRequest,
    PasswordResetConfirm,
    RegisterRequest,
    TwoFactorDisableRequest,
    TwoFactorSetupRequest,
)
from src.api.schemas.users import (
    MAX_SOCIAL_LINKS,
    UserDeletionRequest,
    UserUpdateRequest,
)


ADMIN_PASSWORD_CASES = [
    (
        AdminChangePasswordRequest,
        "current_password",
        {"current_password": "ValidPass123!", "new_password": "ValidPass123!"},
        12,
    ),
    (
        AdminChangePasswordRequest,
        "new_password",
        {"current_password": "ValidPass123!", "new_password": "ValidPass123!"},
        12,
    ),
    (
        AdminOTPSetupBeginRequest,
        "current_password",
        {"current_password": "ValidPass123!"},
        1,
    ),
    (
        AdminOTPDisableRequest,
        "current_password",
        {"current_password": "ValidPass123!", "code": "123456"},
        1,
    ),
]

PUBLIC_PASSWORD_CASES = [
    (
        RegisterRequest,
        "password",
        {
            "username": "tester",
            "email": "tester@example.com",
            "password": "ValidPass123!",
        },
    ),
    (LoginRequest, "password", {"username": "tester", "password": "ValidPass123!"}),
    (TwoFactorSetupRequest, "password", {"password": "ValidPass123!"}),
    (
        TwoFactorDisableRequest,
        "password",
        {"password": "ValidPass123!", "code": "123456"},
    ),
    (
        PasswordResetConfirm,
        "new_password",
        {"token": "reset-token", "new_password": "ValidPass123!"},
    ),
]


class TestAdminPasswordBounds:
    @pytest.mark.parametrize("model,field,payload,_min", ADMIN_PASSWORD_CASES)
    def test_oversized_password_is_rejected(self, model, field, payload, _min):
        with pytest.raises(ValidationError):
            model(**{**payload, field: "x" * 201})

    @pytest.mark.parametrize("model,field,payload,_min", ADMIN_PASSWORD_CASES)
    def test_password_at_the_login_bound_is_accepted(self, model, field, payload, _min):
        model(**{**payload, field: "x" * 200})

    @pytest.mark.parametrize("model,field,payload,_min", ADMIN_PASSWORD_CASES)
    def test_empty_password_is_rejected(self, model, field, payload, _min):
        with pytest.raises(ValidationError):
            model(**{**payload, field: ""})

    def test_admin_login_bound_is_the_one_we_mirror(self):
        AdminLoginRequest(username="admin", password="x" * 200)
        with pytest.raises(ValidationError):
            AdminLoginRequest(username="admin", password="x" * 201)


class TestPublicPasswordBounds:
    @pytest.mark.parametrize("model,field,payload", PUBLIC_PASSWORD_CASES)
    def test_oversized_password_is_rejected_before_hashing(self, model, field, payload):
        with pytest.raises(ValidationError):
            model(**{**payload, field: "x" * (MAX_PASSWORD_INPUT_LENGTH + 1)})

    @pytest.mark.parametrize("model,field,payload", PUBLIC_PASSWORD_CASES)
    def test_password_within_the_structural_ceiling_is_accepted(
        self, model, field, payload
    ):
        model(**{**payload, field: "x" * MAX_PASSWORD_INPUT_LENGTH})

    @pytest.mark.parametrize("model,field,payload", PUBLIC_PASSWORD_CASES)
    def test_empty_password_is_rejected(self, model, field, payload):
        with pytest.raises(ValidationError):
            model(**{**payload, field: ""})

    def test_ceiling_is_not_below_the_default_configured_policy(self):
        # core.auth.passwords defaults to a 128-character maximum; the
        # structural ceiling must never reject a policy-valid password.
        assert MAX_PASSWORD_INPUT_LENGTH >= 128

    def test_optional_update_password_keeps_empty_as_no_change(self):
        UserUpdateRequest(password="", current_password="")

    @pytest.mark.parametrize("field", ["password", "current_password"])
    def test_optional_update_password_is_bounded(self, field):
        with pytest.raises(ValidationError):
            UserUpdateRequest(**{field: "x" * (MAX_PASSWORD_INPUT_LENGTH + 1)})

    def test_deletion_password_is_bounded(self):
        with pytest.raises(ValidationError):
            UserDeletionRequest(password="x" * (MAX_PASSWORD_INPUT_LENGTH + 1))
        with pytest.raises(ValidationError):
            UserDeletionRequest(password="")


class TestSocialLinkCountBound:
    def test_bound_matches_the_profile_manager(self):
        from src.core.profiles.manager import ProfileManager

        assert MAX_SOCIAL_LINKS == ProfileManager.MAX_SOCIAL_LINKS

    def test_all_permitted_links_are_accepted(self):
        links = [
            {"platform": "site", "url": "https://example.com"}
            for _ in range(MAX_SOCIAL_LINKS)
        ]
        assert UserUpdateRequest(social_links=links).social_links == links

    def test_one_link_too_many_is_rejected(self):
        links = [
            {"platform": "site", "url": "https://example.com"}
            for _ in range(MAX_SOCIAL_LINKS + 1)
        ]
        with pytest.raises(ValidationError):
            UserUpdateRequest(social_links=links)

    def test_old_oversized_list_is_rejected(self):
        links = [{"platform": "site", "url": "https://example.com"} for _ in range(20)]
        with pytest.raises(ValidationError):
            UserUpdateRequest(social_links=links)
