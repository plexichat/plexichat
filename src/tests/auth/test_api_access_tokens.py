"""Tests for API access token management."""

import time


class TestAPIAccessTokens:
    """Focused tests for alpha access token lifecycle and policy controls."""

    def test_create_and_list_access_token(self, auth_manager):
        token = auth_manager.create_api_access_token(
            "Alpha Wave 1",
            created_by=1,
            description="Closed alpha cohort",
            scope_mode="monitor",
        )

        listed = auth_manager.list_api_access_tokens()

        assert token.token is not None
        assert any(item.id == token.id for item in listed)
        created = next(item for item in listed if item.id == token.id)
        assert created.description == "Closed alpha cohort"
        assert created.scope_mode == "monitor"
        assert created.use_count_total == 0

    def test_verify_access_token_tracks_usage_details(self, auth_manager):
        token = auth_manager.create_api_access_token("Tracked", created_by=1)

        valid = auth_manager.verify_api_access_token(
            token.token,
            ip_address="203.0.113.10",
            user_agent="pytest-agent",
            path="/api/v1/users/@me",
            method="GET",
        )

        detail = auth_manager.get_api_access_token_usage(token.id)

        assert valid is True
        assert detail["token"].use_count_total >= 1
        assert detail["token"].last_used_ip_address == "203.0.113.10"
        assert detail["token"].last_used_path == "/api/v1/users/@me"
        assert detail["distinct_ip_count"] == 1
        assert detail["recent_events"][0]["allowed"] is True

    def test_access_token_scope_monitor_mode_allows_with_denied_visibility(
        self, auth_manager
    ):
        token = auth_manager.create_api_access_token(
            "Monitor",
            created_by=1,
            scope_mode="monitor",
        )
        auth_manager.add_api_access_token_scope(token.id, "ip", "198.51.100.5", 1)

        valid = auth_manager.verify_api_access_token(
            token.token,
            ip_address="203.0.113.7",
            user_agent="pytest-agent",
            path="/api/v1/messages",
            method="POST",
        )
        detail = auth_manager.get_api_access_token_usage(token.id)

        assert valid is True
        assert detail["recent_events"][0]["allowed"] is True
        assert detail["recent_events"][0]["scope_match"] is False

    def test_access_token_scope_enforce_mode_blocks_mismatched_ip(self, auth_manager):
        token = auth_manager.create_api_access_token(
            "Enforced",
            created_by=1,
            scope_mode="enforce",
        )
        auth_manager.add_api_access_token_scope(token.id, "cidr", "198.51.100.0/24", 1)

        valid = auth_manager.verify_api_access_token(
            token.token,
            ip_address="203.0.113.8",
            user_agent="pytest-agent",
            path="/api/v1/messages",
            method="POST",
        )
        detail = auth_manager.get_api_access_token_usage(token.id)

        assert valid is False
        assert detail["denied_count_total"] == 1
        assert detail["recent_events"][0]["allowed"] is False
        assert detail["recent_events"][0]["reject_reason"] == "ip_scope_denied"

    def test_access_token_expiry_blocks_use(self, auth_manager):
        token = auth_manager.create_api_access_token(
            "Expiring",
            created_by=1,
            expires_at=int(time.time() * 1000) - 1000,
        )

        valid = auth_manager.verify_api_access_token(
            token.token,
            ip_address="203.0.113.9",
            user_agent="pytest-agent",
            path="/api/v1/users/@me",
            method="GET",
        )

        assert valid is False

    def test_rotate_access_token_clones_policy_and_revokes_old(self, auth_manager):
        token = auth_manager.create_api_access_token(
            "Rotate Me",
            created_by=1,
            description="rotation source",
            expires_at=int(time.time() * 1000) + 3600_000,
            scope_mode="enforce",
        )
        auth_manager.add_api_access_token_scope(token.id, "ip", "203.0.113.15", 1)

        rotated = auth_manager.rotate_api_access_token(token.id, rotated_by=2)

        old_token = auth_manager.get_api_access_token(token.id)
        new_scopes = auth_manager.list_api_access_token_scopes(rotated.id)

        assert rotated is not None
        assert rotated.id != token.id
        assert rotated.description == "rotation source"
        assert rotated.scope_mode == "enforce"
        assert old_token.revoked is True
        assert len(new_scopes) == 1
        assert new_scopes[0]["value"] == "203.0.113.15"
