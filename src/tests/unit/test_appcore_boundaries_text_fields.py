"""Bounded application text and informational URL fields.

Names, descriptions and homepage-style links are copied into API responses and
bot directories. The core helpers are the authoritative caps; the HTTP schema
and the manager/bot-profile paths must all apply them.
"""

from pathlib import Path

import pytest

from src.core.applications.validation import (
    MAX_APP_DESCRIPTION_LENGTH,
    MAX_APP_NAME_LENGTH,
    MAX_APP_SHORT_DESCRIPTION_LENGTH,
    MAX_HOMEPAGE_URL_LENGTH,
    validate_application_description,
    validate_application_name,
    validate_homepage_url,
)
from src.core.security.hardening import HardeningViolation

_ROUTE_SOURCE = (
    Path(__file__).resolve().parents[3] / "src" / "api" / "routes" / "applications.py"
).read_text(encoding="utf-8")


class TestNameBounds:
    def test_normalises_valid_name(self):
        assert validate_application_name("  My App  ") == "My App"

    @pytest.mark.parametrize("name", ["", "   ", "a", "\x00good", "ok\nname"])
    def test_rejects_bad_names(self, name):
        with pytest.raises(HardeningViolation):
            validate_application_name(name)

    def test_length_ceiling(self):
        assert validate_application_name("a" * MAX_APP_NAME_LENGTH)
        with pytest.raises(HardeningViolation):
            validate_application_name("a" * (MAX_APP_NAME_LENGTH + 1))


class TestDescriptionBounds:
    def test_optional_and_bounded(self):
        assert validate_application_description(None) is None
        assert validate_application_description("hello") == "hello"
        assert validate_application_description("a" * MAX_APP_DESCRIPTION_LENGTH)
        with pytest.raises(HardeningViolation):
            validate_application_description("a" * (MAX_APP_DESCRIPTION_LENGTH + 1))

    def test_control_characters_rejected(self):
        with pytest.raises(HardeningViolation):
            validate_application_description("hello\r\nX-Injected: 1")

    def test_custom_ceiling_for_short_description(self):
        text = "a" * MAX_APP_SHORT_DESCRIPTION_LENGTH
        assert validate_application_description(
            text, field="short_description", max_length=MAX_APP_SHORT_DESCRIPTION_LENGTH
        )
        with pytest.raises(HardeningViolation):
            validate_application_description(
                text + "a",
                field="short_description",
                max_length=MAX_APP_SHORT_DESCRIPTION_LENGTH,
            )


class TestHomepageUrlBounds:
    @pytest.mark.parametrize("url", ["https://example.com", "http://example.org/docs"])
    def test_accepts_absolute_links(self, url):
        assert validate_homepage_url(url) == url

    @pytest.mark.parametrize(
        "url",
        [
            "/relative",
            "javascript:alert(1)",
            "https://user:pass@example.com",
            "not a url",
            "https://example.com/" + "a" * MAX_HOMEPAGE_URL_LENGTH,
        ],
    )
    def test_rejects_unsafe_or_oversized_links(self, url):
        with pytest.raises(HardeningViolation):
            validate_homepage_url(url)

    def test_none_is_allowed(self):
        assert validate_homepage_url(None) is None


class TestHttpBoundaryWiring:
    """Read the route as source so the assertion is not blocked by unrelated
    route modules importing at collection time."""

    def test_both_request_models_validate_description(self):
        assert _ROUTE_SOURCE.count("_validate_description(value)") >= 2
        assert '@field_validator("description")' in _ROUTE_SOURCE
        assert "validate_application_description" in _ROUTE_SOURCE
        assert "max_length=2000" in _ROUTE_SOURCE


class TestManagerBoundary:
    def test_create_rejects_oversized_description(self, app_manager, test_user):
        with pytest.raises(HardeningViolation):
            app_manager.create_application(
                owner_id=test_user.id,
                name="Long Description",
                description="a" * (MAX_APP_DESCRIPTION_LENGTH + 1),
            )

    def test_create_rejects_unsafe_tos_url(self, app_manager, test_user):
        with pytest.raises(HardeningViolation):
            app_manager.create_application(
                owner_id=test_user.id,
                name="Bad ToS",
                terms_of_service_url="javascript:alert(1)",
            )

    def test_update_rejects_oversized_description(self, app_manager, test_user):
        app = app_manager.create_application(owner_id=test_user.id, name="Bounded App")
        with pytest.raises(HardeningViolation):
            app_manager.update_application(
                user_id=test_user.id,
                application_id=app.id,
                description="a" * (MAX_APP_DESCRIPTION_LENGTH + 1),
            )

    def test_bot_profile_rejects_unsafe_website(self, app_manager, test_user):
        app = app_manager.create_application(
            owner_id=test_user.id, name="Bot Profile App"
        )
        with pytest.raises(HardeningViolation):
            app_manager.update_bot_profile(
                application_id=app.id,
                user_id=test_user.id,
                website_url="ftp://example.com",
            )
