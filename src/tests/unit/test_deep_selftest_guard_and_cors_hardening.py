"""Regression hardening for two deep security invariants.

1. The self-test non-destructive denylist must reject unscoped whole-table
   DELETEs even when they carry a trailing statement terminator or SQL comment.
2. A wildcard CORS origin may never be combined with credentialed requests:
   Starlette would reflect every Origin, turning the API into a cross-origin
   credential-harvesting primitive.
"""

import pytest


@pytest.mark.unit
class TestNonDestructiveGuard:
    def test_unscoped_delete_cannot_hide_behind_suffixes(self):
        from src.core.selftest.db_guard import assert_non_destructive

        for statement in (
            "DELETE FROM auth_users",
            "DELETE FROM auth_users;",
            "DELETE FROM auth_users ; SELECT 1",
            "DELETE FROM auth_users -- looks scoped",
            "DELETE FROM auth_users /* looks scoped */",
            'DELETE FROM "auth_users";',
            "DELETE FROM `auth_users`;",
        ):
            with pytest.raises(ValueError):
                assert_non_destructive(statement)

    def test_scoped_deletes_are_still_accepted(self):
        from src.core.selftest.db_guard import assert_non_destructive

        assert_non_destructive(
            "DELETE FROM auth_users WHERE id = ? AND username LIKE 'selftest_%'"
        )
        assert_non_destructive(
            "DELETE FROM admin_role_assignments WHERE admin_id = ? "
            "AND admin_id IN (SELECT id FROM auth_users "
            "WHERE username LIKE 'selftest_%')"
        )


@pytest.mark.unit
class TestWildcardCorsCredentialsGuard:
    @pytest.fixture(autouse=True)
    def _reset_api_config_cache(self):
        from src.api import config as api_config

        api_config.clear_api_config_cache()
        yield
        api_config.clear_api_config_cache()

    @staticmethod
    def _clean_api_config(monkeypatch, api_conf):
        import utils.config as config

        from src.api import config as api_config

        monkeypatch.setattr(
            config,
            "get",
            lambda key, default=None: api_conf if key == "api" else default,
        )
        api_config.clear_api_config_cache()
        return api_config.get_api_config()

    def test_wildcard_with_credentials_fails_closed(self, monkeypatch):
        cleaned = self._clean_api_config(
            monkeypatch,
            {
                "cors_origins": ["*"],
                "allow_wildcard_cors": True,
                "cors_allow_credentials": True,
            },
        )
        assert cleaned.cors_origins == []
        assert cleaned.cors_allow_credentials is True

    def test_wildcard_without_credentials_is_still_honoured(self, monkeypatch):
        cleaned = self._clean_api_config(
            monkeypatch,
            {
                "cors_origins": ["*"],
                "allow_wildcard_cors": True,
                "cors_allow_credentials": False,
            },
        )
        assert cleaned.cors_origins == ["*"]
        assert cleaned.cors_allow_credentials is False

    def test_wildcard_without_optin_fails_closed(self, monkeypatch):
        cleaned = self._clean_api_config(
            monkeypatch,
            {
                "cors_origins": ["*"],
                "allow_wildcard_cors": False,
                "cors_allow_credentials": False,
            },
        )
        assert cleaned.cors_origins == []

    def test_explicit_origins_with_credentials_are_unchanged(self, monkeypatch):
        cleaned = self._clean_api_config(
            monkeypatch,
            {
                "cors_origins": ["https://app.example.test"],
                "allow_wildcard_cors": True,
                "cors_allow_credentials": True,
            },
        )
        assert cleaned.cors_origins == ["https://app.example.test"]
        assert cleaned.cors_allow_credentials is True
