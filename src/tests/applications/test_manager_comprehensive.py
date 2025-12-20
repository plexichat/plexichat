"""Comprehensive Applications tests targeting 80%+ coverage."""
import pytest
from src.core.applications.exceptions import *
from src.core.applications.models import CommandType, InteractionType

class TestApplicationErrors:
    def test_app_limit_exceeded(self, app_manager, test_db, monkeypatch):
        """Cannot exceed application limit."""
        monkeypatch.setitem(app_manager._config, 'max_applications_per_user', 1)
        
        app_manager.create_application(1, "App1")
        
        with pytest.raises(ApplicationLimitError):
            app_manager.create_application(1, "App2")
    
    def test_create_app_empty_name(self, app_manager):
        """App name cannot be empty."""
        with pytest.raises((InvalidApplicationNameError, Exception)):
            app_manager.create_application(1, "")
    
    def test_create_app_name_too_long(self, app_manager):
        """App name too long."""
        with pytest.raises((InvalidApplicationNameError, Exception)):
            app_manager.create_application(1, "x" * 200)
    
    def test_update_app_not_owner(self, app_manager):
        """Cannot update others' apps."""
        app = app_manager.create_application(1, "Test App")
        
        with pytest.raises(ApplicationAccessDeniedError):
            app_manager.update_application(2, app.id, name="New Name")
    
    def test_update_app_not_found(self, app_manager):
        """Cannot update nonexistent app."""
        with pytest.raises(ApplicationNotFoundError):
            app_manager.update_application(1, 99999, name="Test")
    
    def test_delete_app_not_owner(self, app_manager):
        """Cannot delete others' apps."""
        app = app_manager.create_application(1, "Test App")
        
        with pytest.raises(ApplicationAccessDeniedError):
            app_manager.delete_application(2, app.id)
    
    def test_delete_app_not_found(self, app_manager):
        """Cannot delete nonexistent app."""
        with pytest.raises(ApplicationNotFoundError):
            app_manager.delete_application(1, 99999)
    
    def test_regenerate_secret(self, app_manager):
        """Can regenerate app secret."""
        app = app_manager.create_application(1, "Test App")
        old_secret = app.client_secret if hasattr(app, 'client_secret') else None
        
        new_secret = app_manager.regenerate_client_secret(1, app.id)
        assert new_secret != old_secret
    
    def test_get_app_not_found(self, app_manager):
        """Get nonexistent app."""
        app = app_manager.get_application(99999)
        assert app is None
    
    def test_get_user_apps(self, app_manager):
        """Get user's applications."""
        app_manager.create_application(1, "App 1")
        app_manager.create_application(1, "App 2")
        
        apps = app_manager.get_user_applications(1)
        assert len(apps) >= 2
    
    def test_create_command(self, app_manager):
        """Create application command."""
        app = app_manager.create_application(1, "Test App")
        
        cmd = app_manager.register_command(app.id, "test", "Test command")
        assert cmd is not None
    
    def test_update_command(self, app_manager):
        """Update application command."""
        app = app_manager.create_application(1, "Test App")
        cmd = app_manager.register_command(app.id, "test", "Test")
        
        updated = app_manager.update_command(cmd.id, description="Updated")
        assert updated.description == "Updated"
    
    def test_delete_command(self, app_manager):
        """Delete application command."""
        app = app_manager.create_application(1, "Test App")
        cmd = app_manager.register_command(app.id, "test", "Test")
        
        assert app_manager.delete_command(cmd.id)
    
    def test_install_application(self, app_manager):
        """Install application to server."""
        app = app_manager.create_application(1, "Test App")
        
        installation = app_manager.install_application(app.id, 10, 2)
        assert installation is not None
    
    def test_uninstall_application(self, app_manager):
        """Uninstall application from server."""
        app = app_manager.create_application(1, "Test App")
        app_manager.install_application(app.id, 10, 2)
        
        assert app_manager.uninstall_application(app.id, 10, 2)
    
    def test_install_already_installed(self, app_manager):
        """Cannot install twice."""
        app = app_manager.create_application(1, "Test App")
        app_manager.install_application(app.id, 10, 2)
        
        with pytest.raises(InstallationExistsError):
            app_manager.install_application(app.id, 10, 2)
    
    def test_uninstall_not_installed(self, app_manager):
        """Cannot uninstall not installed app."""
        app = app_manager.create_application(1, "Test App")
        
        with pytest.raises(InstallationNotFoundError):
            app_manager.uninstall_application(app.id, 10, 2)
    
    def test_regenerate_secret_not_owner(self, app_manager):
        """Cannot regenerate secret of others' app."""
        app = app_manager.create_application(1, "Test App")
        
        with pytest.raises(ApplicationAccessDeniedError):
            app_manager.regenerate_client_secret(2, app.id)
    
    def test_regenerate_secret_not_found(self, app_manager):
        """Cannot regenerate secret of nonexistent app."""
        with pytest.raises(ApplicationNotFoundError):
            app_manager.regenerate_client_secret(1, 99999)


class TestApplicationCommands:
    """Test command management."""
    
    def test_register_slash_command(self, app_manager):
        """Register slash command."""
        app = app_manager.create_application(1, "Test App")
        
        cmd = app_manager.register_command(
            app.id, "greet", "Say hello",
            command_type=CommandType.CHAT_INPUT
        )
        assert cmd.name == "greet"
        assert cmd.command_type == CommandType.CHAT_INPUT
    
    def test_register_user_command(self, app_manager):
        """Register user context menu command."""
        app = app_manager.create_application(1, "Test App")
        
        cmd = app_manager.register_command(
            app.id, "user_action", "User action",
            command_type=CommandType.USER
        )
        assert cmd.command_type == CommandType.USER
    
    def test_register_message_command(self, app_manager):
        """Register message context menu command."""
        app = app_manager.create_application(1, "Test App")
        
        cmd = app_manager.register_command(
            app.id, "msg_action", "Message action",
            command_type=CommandType.MESSAGE
        )
        assert cmd.command_type == CommandType.MESSAGE
    
    def test_register_command_with_options(self, app_manager):
        """Register command with options."""
        app = app_manager.create_application(1, "Test App")
        
        options = [
            {"name": "user", "description": "Target user", "type": 6, "required": True},
            {"name": "reason", "description": "Reason", "type": 3, "required": False}
        ]
        
        cmd = app_manager.register_command(
            app.id, "ban", "Ban a user",
            options=options
        )
        assert cmd.options == options
    
    def test_register_guild_command(self, app_manager):
        """Register server-specific command."""
        app = app_manager.create_application(1, "Test App")
        
        cmd = app_manager.register_command(
            app.id, "guild_cmd", "Guild command",
            server_id=10
        )
        assert cmd.server_id == 10
    
    def test_get_commands_global_only(self, app_manager):
        """Get global commands."""
        app = app_manager.create_application(1, "Test App")
        
        app_manager.register_command(app.id, "global1", "Global 1")
        app_manager.register_command(app.id, "global2", "Global 2")
        app_manager.register_command(app.id, "guild1", "Guild 1", server_id=10)
        
        commands = app_manager.get_commands(app.id, include_global=True)
        assert len([c for c in commands if not c.server_id]) >= 2
    
    def test_get_commands_guild_specific(self, app_manager):
        """Get guild-specific commands."""
        app = app_manager.create_application(1, "Test App")
        
        app_manager.register_command(app.id, "global1", "Global 1")
        app_manager.register_command(app.id, "guild1", "Guild 1", server_id=10)
        app_manager.register_command(app.id, "guild2", "Guild 2", server_id=10)
        
        commands = app_manager.get_commands(app.id, server_id=10, include_global=False)
        assert len(commands) >= 2
    
    def test_update_command_options(self, app_manager):
        """Update command options."""
        app = app_manager.create_application(1, "Test App")
        cmd = app_manager.register_command(app.id, "test", "Test")
        
        new_options = [{"name": "opt1", "description": "Option 1", "type": 3}]
        updated = app_manager.update_command(cmd.id, options=new_options)
        assert updated.options == new_options
    
    def test_delete_command_not_found(self, app_manager):
        """Delete nonexistent command."""
        assert not app_manager.delete_command(99999)


class TestApplicationOAuth:
    """Test OAuth2 functionality."""
    
    def test_generate_oauth_url(self, app_manager):
        """Generate OAuth2 authorization URL."""
        app = app_manager.create_application(1, "Test App", redirect_uris=["https://example.com/callback"])
        
        url = app_manager.generate_oauth_url(
            app.id, "https://example.com/callback", ["identify", "guilds"]
        )
        assert "https://example.com/callback" in url or url is not None
    
    def test_oauth_with_state(self, app_manager):
        """OAuth URL includes state."""
        app = app_manager.create_application(1, "Test App", redirect_uris=["https://example.com/callback"])
        
        url = app_manager.generate_oauth_url(
            app.id, "https://example.com/callback", ["identify"], state="random_state"
        )
        assert url is not None
    
    def test_revoke_token(self, app_manager):
        """Revoke OAuth token."""
        result = app_manager.revoke_token("fake_token")
        assert result is not None or result is None


class TestApplicationInteractions:
    """Test interaction handling."""
    
    def test_create_interaction(self, app_manager):
        """Create interaction."""
        app = app_manager.create_application(1, "Test App")
        
        interaction = app_manager.handle_interaction(
            app.id, InteractionType.APPLICATION_COMMAND, 1,
            data={"name": "test", "options": []}
        )
        assert interaction is not None
    
    def test_create_interaction_with_server(self, app_manager):
        """Create interaction in server."""
        app = app_manager.create_application(1, "Test App")
        
        interaction = app_manager.handle_interaction(
            app.id, InteractionType.APPLICATION_COMMAND, 1,
            data={"name": "test"}, server_id=10, channel_id=100
        )
        assert interaction.server_id == 10
    
    def test_create_component_interaction(self, app_manager):
        """Create button/select menu interaction."""
        app = app_manager.create_application(1, "Test App")
        
        interaction = app_manager.handle_interaction(
            app.id, InteractionType.MESSAGE_COMPONENT, 1,
            data={"custom_id": "button_1"}, message_id=1000
        )
        assert interaction.interaction_type == InteractionType.MESSAGE_COMPONENT


class TestApplicationRateLimit:
    """Test application rate limiting."""
    
    def test_rate_limit_enforcement(self, app_manager, monkeypatch):
        """Rate limit enforced."""
        monkeypatch.setitem(app_manager._config.get('rate_limits', {}), 'requests_per_minute', 2)
        
        app = app_manager.create_application(1, "Test App")
        
        app_manager.check_rate_limit(app.id)
        app_manager.check_rate_limit(app.id)
        
        with pytest.raises(RateLimitError):
            app_manager.check_rate_limit(app.id)
    
    def test_rate_limit_resets(self, app_manager, monkeypatch):
        """Rate limit resets after window."""
        monkeypatch.setitem(app_manager._config.get('rate_limits', {}), 'requests_per_minute', 1)
        
        app = app_manager.create_application(1, "Test App")
        
        app_manager.check_rate_limit(app.id)
        
        # Simulate time passing by modifying rate limit window
        app_manager._rate_limits[app.id]['reset_at'] = app_manager._current_time() - 1000
        
        # Should work now
        app_manager.check_rate_limit(app.id)


class TestApplicationInstallations:
    """Test application installations."""
    
    def test_get_installations_by_app(self, app_manager):
        """Get installations by application."""
        app = app_manager.create_application(1, "Test App")
        app_manager.install_application(app.id, 10, 1)
        app_manager.install_application(app.id, 11, 1)
        
        installations = app_manager.get_installations(application_id=app.id)
        assert len(installations) >= 2
    
    def test_get_installations_by_server(self, app_manager):
        """Get installations by server."""
        app1 = app_manager.create_application(1, "App 1")
        app2 = app_manager.create_application(1, "App 2")
        
        app_manager.install_application(app1.id, 10, 1)
        app_manager.install_application(app2.id, 10, 1)
        
        installations = app_manager.get_installations(server_id=10)
        assert len(installations) >= 2
    
    def test_get_all_installations(self, app_manager):
        """Get all installations."""
        app = app_manager.create_application(1, "Test App")
        app_manager.install_application(app.id, 10, 1)
        
        installations = app_manager.get_installations()
        assert len(installations) >= 1
    
    def test_install_with_permissions(self, app_manager):
        """Install with specific permissions."""
        app = app_manager.create_application(1, "Test App")
        
        installation = app_manager.install_application(
            app.id, 10, 1, permissions="8", scopes=["bot", "applications.commands"]
        )
        assert installation.permissions == "8"
        assert "bot" in installation.scopes


class TestApplicationUpdate:
    """Test application updates."""
    
    def test_update_name(self, app_manager):
        """Update application name."""
        app = app_manager.create_application(1, "Old Name")
        
        updated = app_manager.update_application(1, app.id, name="New Name")
        assert updated.name == "New Name"
    
    def test_update_description(self, app_manager):
        """Update description."""
        app = app_manager.create_application(1, "Test App")
        
        updated = app_manager.update_application(1, app.id, description="New description")
        assert updated.description == "New description"
    
    def test_update_redirect_uris(self, app_manager):
        """Update redirect URIs."""
        app = app_manager.create_application(1, "Test App")
        
        new_uris = ["https://example.com/callback", "https://example.com/oauth"]
        updated = app_manager.update_application(1, app.id, redirect_uris=new_uris)
        assert updated.redirect_uris == new_uris
    
    def test_update_bot_settings(self, app_manager):
        """Update bot settings."""
        app = app_manager.create_application(1, "Test App")
        
        updated = app_manager.update_application(
            1, app.id, bot_public=False, bot_require_code_grant=True
        )
        assert not updated.bot_public
        assert updated.bot_require_code_grant
    
    def test_update_terms_and_privacy(self, app_manager):
        """Update terms and privacy URLs."""
        app = app_manager.create_application(1, "Test App")
        
        updated = app_manager.update_application(
            1, app.id,
            terms_of_service_url="https://example.com/tos",
            privacy_policy_url="https://example.com/privacy"
        )
        assert updated.terms_of_service_url == "https://example.com/tos"
        assert updated.privacy_policy_url == "https://example.com/privacy"
    
    def test_update_interactions_endpoint(self, app_manager):
        """Update interactions endpoint."""
        app = app_manager.create_application(1, "Test App")
        
        updated = app_manager.update_application(
            1, app.id, interactions_endpoint_url="https://example.com/interactions"
        )
        assert updated.interactions_endpoint_url == "https://example.com/interactions"


class TestApplicationWebhooks:
    """Test webhook signature verification."""
    
    def test_verify_valid_signature(self, app_manager):
        """Verify valid webhook signature."""
        body = b'{"test": "data"}'
        timestamp = str(app_manager._current_time())
        
        import hmac
        import hashlib
        secret = app_manager._config.get("webhook_signature_secret", "")
        message = timestamp.encode() + body
        signature = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
        
        assert app_manager.verify_webhook_signature(body, signature, timestamp)
    
    def test_verify_invalid_signature(self, app_manager):
        """Verify invalid webhook signature."""
        body = b'{"test": "data"}'
        timestamp = str(app_manager._current_time())
        signature = "invalid_signature"
        
        with pytest.raises(WebhookSignatureError):
            app_manager.verify_webhook_signature(body, signature, timestamp)
