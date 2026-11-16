"""Cross-cutting regressions for the application/feature boundary sweep.

The nine hardening waves each added a focused test module. This file pins the
properties that only show up when they are considered together: every guard
uses the shared hardening rules, every cap is ordered and positive, failures
stay ``ValueError``-compatible for the API, and a fully valid application still
round-trips through the manager unchanged.

Only ``src.core.security.hardening`` and ``src.api.request_guards`` are imported
from outside the owned packages; neither is modified by these tests.
"""

from pathlib import Path

import pytest

from src.api import request_guards
from src.core.security.hardening import (
    HardeningViolation,
    bounded_int,
    reject_control_characters,
    require_https_url,
    require_http_url,
)
from src.core.applications.validation import (
    MAX_APPLICATION_FILTER_LENGTH,
    MAX_APP_DESCRIPTION_LENGTH,
    MAX_APP_NAME_LENGTH,
    MAX_HOMEPAGE_URL_LENGTH,
    MAX_REDIRECT_URIS,
    MAX_REDIRECT_URI_LENGTH,
    MAX_URL_PATTERN_LENGTH,
    MAX_WEBHOOK_URL_LENGTH,
    require_oauth_redirect_url,
    validate_application_description,
    validate_application_name,
    validate_config_url_pattern,
    validate_homepage_url,
    validate_redirect_uris,
    validate_webhook_url,
)
from src.core.applications.exceptions import InvalidApplicationNameError
from src.core.applications.oauth.scopes import (
    MAX_SCOPES_PER_REQUEST,
    MAX_SCOPE_LENGTH,
    require_valid_scopes,
    validate_permission_string,
)
from src.core.applications.oauth.tokens import (
    MAX_TOKEN_LENGTH,
    validate_client_secret,
    validate_token_string,
)
from src.core.features.validation import (
    MAX_FEATURE_VALUE_LENGTH,
    MAX_FLAGS_PER_REQUEST,
    MAX_ROLLOUT_PERCENTAGE,
    MAX_ROLLOUT_TARGETS,
    feature_enabled_for,
    validate_feature_flags,
    validate_feature_key,
    validate_feature_value,
    validate_rollout_percentage,
    validate_tier_limits,
)

_REPO = Path(__file__).resolve().parents[3]


def _source(relative: str) -> str:
    return (_REPO / relative).read_text(encoding="utf-8")


class TestSharedRuleAdoption:
    OWNED_MODULES = (
        "src/core/applications/validation.py",
        "src/core/applications/oauth/scopes.py",
        "src/core/applications/oauth/tokens.py",
        "src/core/applications/oauth/flows.py",
        "src/core/applications/manager/application_crud.py",
        "src/core/applications/manager/base.py",
        "src/core/applications/manager/webhook.py",
        "src/core/features/validation.py",
        "src/core/features/__init__.py",
    )

    @pytest.mark.parametrize("relative", OWNED_MODULES)
    def test_owned_modules_use_shared_hardening(self, relative):
        assert "src.core.security.hardening" in _source(relative)

    def test_http_routes_import_core_validation(self):
        route_source = _source("src/api/routes/applications.py")
        assert "src.core.applications.validation" in route_source
        caps_source = _source("src/api/routes/capabilities.py")
        assert "src.core.security.hardening" in caps_source

    def test_request_guards_imported_not_modified(self):
        # Importing (not patching) these helpers keeps the raw-body caps in the
        # test's dependency graph without changing their behaviour.
        assert request_guards.MAX_FEDERATION_BODY > 0
        assert callable(request_guards.read_body_capped)
        assert callable(request_guards.read_upload_capped)


class TestCapOrdering:
    def test_application_caps_positive_and_ordered(self):
        assert 0 < MAX_APP_NAME_LENGTH < MAX_APP_DESCRIPTION_LENGTH
        assert MAX_REDIRECT_URIS > 0 and MAX_REDIRECT_URI_LENGTH > 0
        assert MAX_HOMEPAGE_URL_LENGTH >= MAX_WEBHOOK_URL_LENGTH
        assert MAX_URL_PATTERN_LENGTH > 0
        assert MAX_APPLICATION_FILTER_LENGTH < MAX_APP_DESCRIPTION_LENGTH

    def test_oauth_caps_positive(self):
        assert 0 < MAX_SCOPES_PER_REQUEST < MAX_TOKEN_LENGTH
        assert MAX_SCOPE_LENGTH > 0
        assert MAX_TOKEN_LENGTH > 64

    def test_feature_caps_positive(self):
        assert MAX_ROLLOUT_PERCENTAGE == 100.0
        assert 0 < MAX_ROLLOUT_TARGETS
        assert 0 < MAX_FLAGS_PER_REQUEST
        assert MAX_FEATURE_VALUE_LENGTH > 0


class TestValueErrorCompatibility:
    def test_all_guards_are_value_errors(self):
        assert issubclass(HardeningViolation, ValueError)
        bad_calls = [
            lambda: validate_application_name(""),
            lambda: validate_application_description(
                "x" * (MAX_APP_DESCRIPTION_LENGTH + 1)
            ),
            lambda: validate_homepage_url("relative"),
            lambda: validate_redirect_uris(["http://example.com/cb"]),
            lambda: require_oauth_redirect_url("file:///etc/passwd"),
            lambda: validate_webhook_url("http://example.com/hook"),
            lambda: validate_config_url_pattern("["),
            lambda: require_valid_scopes(["unknown"]),
            lambda: validate_permission_string("-1"),
            lambda: validate_client_secret("bad secret"),
            lambda: validate_token_string("x" * (MAX_TOKEN_LENGTH + 1)),
            lambda: validate_feature_key("__class__"),
            lambda: validate_feature_value([]),
            lambda: validate_feature_flags({"ok": 1, "__class__": 2}),
            lambda: validate_rollout_percentage(101),
            lambda: validate_tier_limits({"max_servers": -1}),
        ]
        for call in bad_calls:
            with pytest.raises(ValueError):
                call()

    def test_shared_primitives_still_enforce_their_rules(self):
        with pytest.raises(HardeningViolation):
            require_https_url("http://example.com")
        with pytest.raises(HardeningViolation):
            require_http_url("javascript:alert(1)")
        with pytest.raises(HardeningViolation):
            reject_control_characters("ok\ninjected")
        with pytest.raises(HardeningViolation):
            bounded_int(10**12, "value", minimum=0, maximum=100)


class TestFullApplicationRoundTrip:
    VALID = "https://example.com/callback"

    def test_valid_payload_is_unchanged(self, app_manager, test_user):
        app = app_manager.create_application(
            owner_id=test_user.id,
            name="Valid App",
            description="A description",
            redirect_uris=[self.VALID],
            terms_of_service_url="https://example.com/tos",
            privacy_policy_url="https://example.com/privacy",
            interactions_endpoint_url="https://example.com/interactions",
        )
        assert app.name == "Valid App"
        assert app.description == "A description"
        assert app.redirect_uris == [self.VALID]
        assert app.terms_of_service_url == "https://example.com/tos"
        assert app.interactions_endpoint_url == "https://example.com/interactions"

        page = app_manager.get_user_applications(test_user.id, name_filter="Valid")
        assert [item.id for item in page] == [app.id]

    @pytest.mark.parametrize(
        "field,value",
        [
            ("name", "x"),
            ("description", "x" * (MAX_APP_DESCRIPTION_LENGTH + 1)),
            ("redirect_uris", ["http://example.com/cb"]),
            ("terms_of_service_url", "javascript:alert(1)"),
            ("interactions_endpoint_url", "http://example.com/hook"),
        ],
    )
    def test_every_field_rejects_its_bad_value(
        self, app_manager, test_user, field, value
    ):
        kwargs = {"name": "Baseline App", field: value}
        expected = (ValueError, InvalidApplicationNameError)
        with pytest.raises(expected):
            app_manager.create_application(owner_id=test_user.id, **kwargs)


class TestRolloutRegression:
    def test_bounded_rollout_is_monotonic(self):
        user = 424242
        results = [
            feature_enabled_for(user, rollout_percentage=pct)
            for pct in (0, 25, 50, 75, 100)
        ]
        assert results == sorted(results)
        assert results[0] is False and results[-1] is True

    def test_oversized_target_list_rejected(self):
        with pytest.raises(HardeningViolation):
            feature_enabled_for(
                1,
                rollout_percentage=50,
                targets=[1] * (MAX_ROLLOUT_TARGETS + 1),
            )


class TestModuleCoverage:
    def test_all_nine_focused_modules_exist(self):
        prefixes = (
            "redirect_uris",
            "text_fields",
            "scopes",
            "tokens",
            "capabilities",
            "feature_flags",
            "rollouts",
            "pagination",
            "webhook_urls",
        )
        names = {
            p.name
            for p in (_REPO / "src/tests/unit").glob("test_appcore_boundaries_*.py")
        }
        for prefix in prefixes:
            assert f"test_appcore_boundaries_{prefix}.py" in names
        assert "test_appcore_boundaries_regressions.py" in names
