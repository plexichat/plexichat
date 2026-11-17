"""Regression tests for the admin/static/selftest deep-hardening sinks.

Every audited sink has a focused assertion here:

* migration version -> migration module loading (RCE/traversal)
* admin template + static asset reads (traversal / MIME confusion)
* destructive maintenance confirmation gates (coerced approval -> mutation)
* self-test config -> path/body/worker sinks (active content / arbitrary write)
"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


@pytest.mark.unit
class TestMigrationVersionConfinement:
    @pytest.mark.parametrize(
        "bad",
        [
            "../../etc/passwd",
            "001.py",
            "001\n",  # re.match(r'^\d{3}$') used to accept this
            "001\x00",
            "1" * 4,
            "",
            None,
            1,
        ],
    )
    def test_shared_validator_rejects_non_digit_versions(self, bad):
        from src.api.routes.admin.utils import validate_migration_version

        with pytest.raises(HTTPException) as exc:
            validate_migration_version(bad)
        assert exc.value.status_code == 400

    def test_database_apply_validates_version_before_manager(self, monkeypatch):
        from src.api.routes.admin import database as routes

        monkeypatch.setattr(routes, "check_host_restriction", lambda request: None)
        monkeypatch.setattr(routes, "require_admin_permission", lambda request, perm: 1)
        monkeypatch.setattr(routes, "require_approved_request", lambda action: 1)

        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                routes.apply_migration(
                    SimpleNamespace(),
                    "../../etc/passwd",
                    confirmation="APPLY MIGRATION",
                )
            )
        assert exc.value.status_code == 400

    def test_database_rollback_validates_version_before_manager(self, monkeypatch):
        from src.api.routes.admin import database as routes

        monkeypatch.setattr(routes, "check_host_restriction", lambda request: None)
        monkeypatch.setattr(routes, "require_admin_permission", lambda request, perm: 1)
        monkeypatch.setattr(routes, "require_approved_request", lambda action: 1)

        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                routes.rollback_migration(
                    SimpleNamespace(), "..\\..\\evil", confirmation="ROLLBACK MIGRATION"
                )
            )
        assert exc.value.status_code == 400


@pytest.mark.unit
class TestStaticAndTemplateConfinement:
    def test_admin_static_path_refuses_traversal(self):
        from src.api.routes.admin.ui import _admin_static_path

        with pytest.raises(ValueError):
            _admin_static_path("..", "..", "etc", "passwd")
        with pytest.raises(ValueError):
            _admin_static_path("/etc/passwd")

    def test_admin_html_responses_set_nosniff(self):
        from src.api.routes.admin.ui import _security_headers

        assert _security_headers("n")["X-Content-Type-Options"] == "nosniff"

    def test_template_allows_only_allowlisted_names(self):
        from src.api.routes.admin.utils import load_admin_template

        assert load_admin_template("../secrets.html") == "<h1>Access Denied</h1>"
        assert load_admin_template("../../config.yaml") == "<h1>Access Denied</h1>"

    def test_template_rejects_nonce_with_attribute_breakout(self):
        from src.api.routes.admin.utils import load_admin_template

        payload = 'abc" onload="alert(1)'
        result = load_admin_template("dashboard.html", csp_nonce=payload)
        assert payload not in result
        assert "onload=" not in result

    def test_robots_response_sets_nosniff(self):
        from src.api.routes.help import robots_txt

        response = asyncio.run(robots_txt())
        assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.unit
class TestDestructiveConfirmation:
    def test_exact_phrase_required_and_dry_run_exempt(self):
        from src.api.routes.admin.utils import require_destructive_confirmation

        require_destructive_confirmation("migrations.rollback", "ROLLBACK MIGRATION")
        require_destructive_confirmation(
            "search.reindex", None, dry_run=True
        )  # incremental reindex mutates nothing destructive
        with pytest.raises(HTTPException) as exc:
            require_destructive_confirmation("migrations.rollback", None)
        assert exc.value.status_code == 400
        with pytest.raises(HTTPException):
            require_destructive_confirmation(
                "migrations.rollback", "REBUILD SEARCH INDEX"
            )  # a confirmation for another action must not authorize this one

    def test_force_reindex_without_confirmation_is_blocked(self, monkeypatch):
        from src.api.routes.admin import reindex as routes

        monkeypatch.setattr(routes, "check_host_restriction", lambda request: None)
        monkeypatch.setattr(routes, "require_admin_permission", lambda request, perm: 1)
        monkeypatch.setattr(routes, "require_approved_request", lambda action: 1)

        class _Manager:
            def reindex_all(self, *args, **kwargs):
                raise AssertionError("destructive reindex must not run")

        monkeypatch.setattr(routes.search_module, "_manager", _Manager(), raising=False)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                routes.reindex_search(
                    SimpleNamespace(), routes.ReindexRequest(force=True)
                )
            )
        assert exc.value.status_code == 400

    def test_migration_rollback_without_confirmation_is_blocked(self, monkeypatch):
        from src.api.routes.admin import migrations as routes

        monkeypatch.setattr(routes, "check_host_restriction", lambda request: None)
        monkeypatch.setattr(routes, "require_admin_permission", lambda request, perm: 1)
        monkeypatch.setattr(routes, "require_approved_request", lambda action: 1)

        class _Manager:
            def rollback_migration(self, version):
                raise AssertionError("rollback must not run")

        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                routes.rollback_migration(SimpleNamespace(), "001", None, _Manager())
            )
        assert exc.value.status_code == 400


@pytest.mark.unit
class TestSelfTestConfigSanitization:
    def test_active_content_and_traversal_are_rejected(self):
        from src.core.selftest.config_guard import sanitize_selftest_config

        cleaned = sanitize_selftest_config(
            {
                "excluded_endpoints": [
                    "/api/v1/auth/logout",
                    "<script>alert(1)</script>",
                    "../../etc/passwd",
                ],
                "parallel_get_workers": 9999,
                "journal_path": "/etc/cron.d/evil.json",
                "template": "{{ 7 * 7 }} and __import__('os')",
                "safe": "selftest_admin",
            }
        )
        assert cleaned["excluded_endpoints"] == ["/api/v1/auth/logout"]
        assert "parallel_get_workers" not in cleaned
        assert "template" not in cleaned
        assert cleaned["safe"] == "selftest_admin"
        assert "cron.d" not in cleaned["journal_path"]

    def test_safe_config_is_preserved(self):
        from src.core.selftest.config_guard import sanitize_selftest_config

        config = {
            "excluded_endpoints": ["/api/v1/admin/logout"],
            "parallel_get_workers": 4,
            "enable_admin_tests": False,
        }
        cleaned = sanitize_selftest_config(config)
        assert cleaned["excluded_endpoints"] == ["/api/v1/admin/logout"]
        assert cleaned["parallel_get_workers"] == 4
        assert cleaned["enable_admin_tests"] is False
        assert "journal_path" in cleaned
