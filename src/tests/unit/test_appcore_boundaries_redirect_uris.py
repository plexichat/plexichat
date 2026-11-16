"""Boundary regressions for OAuth redirect-URI validation.

Redirect URIs are the allow-list OAuth codes are bound to. The shared helper in
``src.core.applications.validation`` is the single source of truth used by the
HTTP schema, the application manager and the OAuth flow, so a bad URI is
rejected at every layer rather than only at the route.
"""

from pathlib import Path

import pytest

from src.core.applications.validation import (
    MAX_REDIRECT_URIS,
    MAX_REDIRECT_URI_LENGTH,
    validate_redirect_uris,
    require_oauth_redirect_url,
)
from src.core.applications.oauth.flows import OAuth2Flow
from src.core.applications.exceptions import InvalidRedirectUriError
from src.core.security.hardening import HardeningViolation

_ROUTE_SOURCE = (
    Path(__file__).resolve().parents[3] / "src" / "api" / "routes" / "applications.py"
).read_text(encoding="utf-8")


VALID = "https://example.com/callback"


class TestRedirectUriHelper:
    def test_accepts_https(self):
        assert require_oauth_redirect_url(VALID) == VALID

    def test_accepts_loopback_http(self):
        for uri in (
            "http://localhost:8000/callback",
            "http://127.0.0.1/callback",
            "http://[::1]/callback",
        ):
            assert require_oauth_redirect_url(uri) == uri

    @pytest.mark.parametrize(
        "uri",
        [
            "http://example.com/callback",  # public plain HTTP
            "https://user:pass@example.com/callback",  # embedded credentials
            "https://example.com/cb#fragment",  # fragment
            "javascript:alert(1)",
            "//example.com/callback",  # scheme-relative
            "/relative/callback",
            "https:///callback",  # no host
            "https://example.com/\n/callback",  # control character
            "",
            None,
        ],
    )
    def test_rejects_unsafe_shapes(self, uri):
        with pytest.raises(HardeningViolation):
            require_oauth_redirect_url(uri)

    def test_list_normalisation_and_caps(self):
        assert validate_redirect_uris(None) == []
        assert validate_redirect_uris([VALID, VALID]) == [VALID]

        with pytest.raises(HardeningViolation):
            validate_redirect_uris([VALID] * (MAX_REDIRECT_URIS + 1))
        with pytest.raises(HardeningViolation):
            validate_redirect_uris(
                ["https://example.com/" + "a" * MAX_REDIRECT_URI_LENGTH]
            )
        with pytest.raises(HardeningViolation):
            validate_redirect_uris(VALID)  # not a list


class TestRedirectUriAtHttpBoundary:
    def test_route_validator_delegates(self):
        assert "_validate_redirect_uris(" in _ROUTE_SOURCE
        assert "return validate_redirect_uris(value)" in _ROUTE_SOURCE
        assert '@field_validator("redirect_uris")' in _ROUTE_SOURCE
        assert "max_length=20" in _ROUTE_SOURCE


class TestRedirectUriAtCoreBoundary:
    def test_manager_rejects_bad_uri_on_create(self, app_manager, test_user):
        with pytest.raises(HardeningViolation):
            app_manager.create_application(
                owner_id=test_user.id,
                name="Bad URI App",
                redirect_uris=["http://example.com/callback"],
            )

    def test_manager_rejects_bad_uri_on_update(self, app_manager, test_user):
        app = app_manager.create_application(
            owner_id=test_user.id, name="Good App", redirect_uris=[VALID]
        )
        with pytest.raises(HardeningViolation):
            app_manager.update_application(
                user_id=test_user.id,
                application_id=app.id,
                redirect_uris=["https://example.com/cb#frag"],
            )

    def _flow(self, app_manager):
        return OAuth2Flow(
            app_manager._db,
            {"code_expiry_seconds": 600, "token_expiry_seconds": 604800},
        )

    def test_flow_rejects_bad_uri_before_registration_check(
        self, app_manager, test_user
    ):
        app = app_manager.create_application(
            owner_id=test_user.id, name="Flow App", redirect_uris=[VALID]
        )
        flow = self._flow(app_manager)
        with pytest.raises(InvalidRedirectUriError):
            flow.validate_authorization_request(
                app.id, "file:///etc/passwd", ["identify"]
            )
        with pytest.raises(InvalidRedirectUriError):
            flow.create_authorization_code(
                app.id, test_user.id, "http://example.com/cb", ["identify"]
            )
