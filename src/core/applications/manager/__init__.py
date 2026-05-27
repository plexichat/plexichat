"""
Application Manager - Core business logic for application operations.

Handles creating, updating, and managing applications with proper
validation, permission checks, and database interactions.
"""

import json
import hmac
import hashlib
from dataclasses import dataclass
from typing import Optional, List, Dict, Any

import utils.config as config
import utils.logger as logger
from src.core.base import BaseManager, SnowflakeID

from ..models import (
    Application,
    Command,
    Interaction,
    ApplicationInstallation,
    CommandType,
    InteractionType,
    InteractionResponse,
    ApprovedBot,
    BotRequest,
    BotProfile,
    BotApprovalStatus,
    UserAuthorizedApplication,
)
from ..exceptions import (
    ApplicationNotFoundError,
    ApplicationAccessDeniedError,
    ApplicationLimitError,
    InvalidApplicationNameError,
    InstallationNotFoundError,
    InstallationExistsError,
    WebhookSignatureError,
    RateLimitError,
    BotLimitError,
    BotRequestError,
    BotRequestExistsError,
    BotAlreadyApprovedError,
    LicenseFeatureError,
    PermissionDeniedError,
)
from ..oauth import OAuth2Flow
from ..oauth.tokens import generate_client_secret
from ..commands import CommandRegistry
from ..interactions import InteractionHandler


class ApplicationManager(BaseManager):
    """Core application manager handling all operations."""

    def __init__(self, db, auth_module=None, servers_module=None, events_module=None):
        """
        Initialize the application manager.

        Args:
            db: Database instance (must be connected)
            auth_module: Auth module for bot account integration
            servers_module: Servers module for installation tracking
            events_module: Events module for interaction dispatch
        """
        super().__init__(db, auth_module)
        self._servers = servers_module
        self._events = events_module
        self._config = self._load_config()

        oauth_config = {
            "token_expiry_seconds": self._config.get("oauth", {}).get(
                "token_expiry_seconds", 604800
            ),
            "code_expiry_seconds": self._config.get("oauth", {}).get(
                "code_expiry_seconds", 600
            ),
            "refresh_enabled": self._config.get("oauth", {}).get(
                "refresh_enabled", True
            ),
            "authorization_endpoint": "/oauth2/authorize",
        }
        self._oauth = OAuth2Flow(db, oauth_config)

        command_config = {
            "max_commands_per_app": self._config.get("command_limits", {}).get(
                "max_commands_per_app", 100
            ),
            "max_options_per_command": self._config.get("command_limits", {}).get(
                "max_options_per_command", 25
            ),
        }
        self._commands = CommandRegistry(db, command_config)

        interaction_config = {
            "interaction_timeout": self._config.get("interaction_timeout", 900),
        }
        self._interactions = InteractionHandler(db, interaction_config, events_module)

        self._rate_limits = {}

        logger.info("Application module initialized")

    def _load_config(self) -> Dict[str, Any]:
        """Load application configuration."""
        defaults = {
            "max_applications_per_user": 25,
            "oauth": {
                "token_expiry_seconds": 604800,
                "code_expiry_seconds": 600,
                "refresh_enabled": True,
                "allowed_redirect_uri_pattern": "^https?://",
            },
            "command_limits": {
                "max_commands_per_app": 100,
                "max_options_per_command": 25,
            },
            "interaction_timeout": 900,
            "rate_limits": {
                "requests_per_minute": 50,
                "burst_limit": 10,
            },
            "webhook_signature_secret": "plexichat-webhook-secret",  # pragma: allowlist secret
        }

        app_config = config.get("applications", {})
        return {**defaults, **app_config}

    def create_application(
        self,
        owner_id: SnowflakeID,
        name: str,
        description: Optional[str] = None,
        redirect_uris: Optional[List[str]] = None,
        bot_public: bool = True,
        bot_require_code_grant: bool = False,
        terms_of_service_url: Optional[str] = None,
        privacy_policy_url: Optional[str] = None,
        interactions_endpoint_url: Optional[str] = None,
    ) -> Application:
        """
        Create a new application.

        Args:
            owner_id: User ID of the owner
            name: Application name
            description: Optional description
            bot_public: Whether the bot can be added by anyone
            bot_require_code_grant: Whether the bot requires OAuth2 code grant
            terms_of_service_url: Optional TOS URL
            privacy_policy_url: Optional privacy policy URL
            redirect_uris: Optional list of redirect URIs
            interactions_endpoint_url: Optional URL for interactions

        Returns:
            Created Application object
        """
        name = self._validate_app_name(name)

        max_apps = self._config.get("max_applications_per_user", 25)
        count = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM app_applications WHERE owner_id = ?",
            (owner_id,),
        )
        current = count["count"] if count else 0

        if current >= max_apps:
            raise ApplicationLimitError(
                f"Maximum of {max_apps} applications per user", max_apps, current
            )

        app_id = self._generate_id()
        now = self._get_timestamp()

        client_secret, secret_hash = generate_client_secret()

        uris_json = json.dumps(redirect_uris or [])

        self._db.execute(
            """INSERT INTO app_applications
               (id, owner_id, name, description, icon_url, bot_id, bot_public,
                bot_require_code_grant, terms_of_service_url, privacy_policy_url,
                redirect_uris, interactions_endpoint_url, client_secret_hash,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                app_id,
                owner_id,
                name,
                description,
                None,
                None,
                1 if bot_public else 0,
                1 if bot_require_code_grant else 0,
                terms_of_service_url,
                privacy_policy_url,
                uris_json,
                interactions_endpoint_url,
                secret_hash,
                now,
                now,
            ),
        )

        logger.info(f"Application created: {name} (ID: {app_id}) by user {owner_id}")

        return Application(
            id=app_id,
            owner_id=owner_id,
            name=name,
            description=description,
            icon_url=None,
            bot_id=None,
            bot_public=bot_public,
            bot_require_code_grant=bot_require_code_grant,
            terms_of_service_url=terms_of_service_url,
            privacy_policy_url=privacy_policy_url,
            redirect_uris=redirect_uris or [],
            interactions_endpoint_url=interactions_endpoint_url,
            created_at=now,
            updated_at=now,
            client_secret=client_secret,
            client_secret_hash=secret_hash,
        )

    def get_application(
        self, application_id: SnowflakeID, user_id: Optional[SnowflakeID] = None
    ) -> Optional[Application]:
        """
        Get an application by ID.

        Args:
            application_id: Application ID
            user_id: Optional user ID for access check

        Returns:
            Application or None
        """
        row = self._db.fetch_one(
            """SELECT id, owner_id, name, description, icon_url, bot_id, bot_public,
                      bot_require_code_grant, terms_of_service_url, privacy_policy_url,
                      redirect_uris, interactions_endpoint_url, created_at, updated_at
               FROM app_applications WHERE id = ?""",
            (application_id,),
        )

        if not row:
            return None

        return self._row_to_application(row)

    def get_user_applications(self, user_id: SnowflakeID) -> List[Application]:
        """
        Get all applications owned by a user.

        Args:
            user_id: User ID

        Returns:
            List of Applications
        """
        rows = self._db.fetch_all(
            """SELECT id, owner_id, name, description, icon_url, bot_id, bot_public,
                      bot_require_code_grant, terms_of_service_url, privacy_policy_url,
                      redirect_uris, interactions_endpoint_url, created_at, updated_at
               FROM app_applications WHERE owner_id = ?
               ORDER BY created_at DESC""",
            (user_id,),
        )

        return [self._row_to_application(row) for row in rows]

    def update_application(
        self,
        user_id: SnowflakeID,
        application_id: SnowflakeID,
        name: Optional[str] = None,
        description: Optional[str] = None,
        icon_url: Optional[str] = None,
        redirect_uris: Optional[List[str]] = None,
        bot_public: Optional[bool] = None,
        bot_require_code_grant: Optional[bool] = None,
        terms_of_service_url: Optional[str] = None,
        privacy_policy_url: Optional[str] = None,
        interactions_endpoint_url: Optional[str] = None,
    ) -> Application:
        """
        Update an application.

        Args:
            user_id: ID of user making the update
            application_id: Application ID
            Other args: Fields to update

        Returns:
            Updated Application

        Raises:
            ApplicationNotFoundError: Application not found
            ApplicationAccessDeniedError: User does not own application
        """
        app = self.get_application(application_id)
        if not app:
            raise ApplicationNotFoundError("Application not found")

        if app.owner_id != user_id:
            raise ApplicationAccessDeniedError("You do not own this application")

        updates = []
        params = []

        if name is not None:
            name = self._validate_app_name(name)
            updates.append("name = ?")
            params.append(name)

        if description is not None:
            updates.append("description = ?")
            params.append(description)

        if icon_url is not None:
            updates.append("icon_url = ?")
            params.append(icon_url)

        if redirect_uris is not None:
            updates.append("redirect_uris = ?")
            params.append(json.dumps(redirect_uris))

        if bot_public is not None:
            updates.append("bot_public = ?")
            params.append(1 if bot_public else 0)

        if bot_require_code_grant is not None:
            updates.append("bot_require_code_grant = ?")
            params.append(1 if bot_require_code_grant else 0)

        if terms_of_service_url is not None:
            updates.append("terms_of_service_url = ?")
            params.append(terms_of_service_url)

        if privacy_policy_url is not None:
            updates.append("privacy_policy_url = ?")
            params.append(privacy_policy_url)

        if interactions_endpoint_url is not None:
            updates.append("interactions_endpoint_url = ?")
            params.append(interactions_endpoint_url)

        if updates:
            updates.append("updated_at = ?")
            params.append(self._get_timestamp())
            params.append(application_id)

            self._db.execute(
                f"UPDATE app_applications SET {', '.join(updates)} WHERE id = ?",
                tuple(params),
            )

            logger.debug(f"Application updated: {application_id}")

        result = self.get_application(application_id)
        assert result is not None  # Should exist since we just updated it
        return result

    def delete_application(
        self, user_id: SnowflakeID, application_id: SnowflakeID
    ) -> bool:
        """
        Delete an application.
        """
        app = self.get_application(application_id, user_id)
        if not app:
            raise ApplicationNotFoundError("Application not found")

        if app.owner_id != user_id:
            raise ApplicationAccessDeniedError(
                "Only the owner can delete the application"
            )

        self._db.execute("DELETE FROM app_applications WHERE id = ?", (application_id,))
        logger.info(f"Application deleted: {application_id}")
        return True

    def _validate_app_name(self, name: str) -> str:
        """Validate and normalize application name."""
        if not name or not name.strip():
            raise InvalidApplicationNameError("Application name cannot be empty")

        name = name.strip()
        if len(name) < 2:
            raise InvalidApplicationNameError(
                "Application name too short (min 2 characters)"
            )
        if len(name) > 100:
            raise InvalidApplicationNameError(
                "Application name too long (max 100 characters)"
            )

        return name

    def regenerate_client_secret(
        self, user_id: SnowflakeID, application_id: SnowflakeID
    ) -> str:
        """
        Regenerate the client secret for an application.

        Args:
            user_id: ID of user
            application_id: Application ID

        Returns:
            New client secret

        Raises:
            ApplicationNotFoundError: Application not found
            ApplicationAccessDeniedError: User does not own application
        """
        app = self.get_application(application_id)
        if not app:
            raise ApplicationNotFoundError("Application not found")

        if app.owner_id != user_id:
            raise ApplicationAccessDeniedError("You do not own this application")

        client_secret, secret_hash = generate_client_secret()

        self._db.execute(
            "UPDATE app_applications SET client_secret_hash = ?, updated_at = ? WHERE id = ?",
            (secret_hash, self._get_timestamp(), application_id),
        )

        logger.info(f"Client secret regenerated for application: {application_id}")
        return client_secret

    def create_bot_for_application(
        self,
        user_id: SnowflakeID,
        application_id: SnowflakeID,
        permissions: Optional[Dict[str, bool]] = None,
    ) -> Dict[str, Any]:
        """
        Create a bot account for an application.

        Args:
            user_id: ID of user
            application_id: Application ID
            permissions: Bot permissions

        Returns:
            Dict with bot info and token

        Raises:
            ApplicationNotFoundError: Application not found
            ApplicationAccessDeniedError: User does not own application
        """
        if not self._auth:
            raise ApplicationNotFoundError("Auth module not available")

        app = self.get_application(application_id)
        if not app:
            raise ApplicationNotFoundError("Application not found")

        if app.owner_id != user_id:
            raise ApplicationAccessDeniedError("You do not own this application")

        if app.bot_id:
            raise ApplicationAccessDeniedError("Application already has a bot")

        bot_username = f"{app.name.lower().replace(' ', '_')}_bot"
        bot = self._auth.create_bot(
            owner_id=user_id,
            username=bot_username,
            display_name=app.name,
            permissions=permissions,
        )

        self._db.execute(
            "UPDATE app_applications SET bot_id = ?, updated_at = ? WHERE id = ?",
            (bot.id, self._get_timestamp(), application_id),
        )

        logger.info(f"Bot created for application {application_id}: {bot.id}")

        return {
            "bot_id": bot.id,
            "bot_username": bot.username,
            "token": bot.token,
        }

    def generate_oauth_url(
        self,
        application_id: SnowflakeID,
        redirect_uri: str,
        scopes: List[str],
        state: Optional[str] = None,
        permissions: Optional[str] = None,
    ) -> str:
        """
        Generate an OAuth2 authorization URL.

        Args:
            application_id: Application ID
            redirect_uri: Redirect URI
            scopes: List of scopes
            state: Optional state parameter
            permissions: Optional bot permissions

        Returns:
            Authorization URL
        """
        return self._oauth.generate_authorization_url(
            application_id, redirect_uri, scopes, state, permissions
        )

    def exchange_code(
        self,
        application_id: SnowflakeID,
        client_secret: str,
        code: str,
        redirect_uri: str,
    ) -> Dict[str, Any]:
        """
        Exchange an authorization code for tokens.

        Args:
            application_id: Application ID
            client_secret: Client secret
            code: Authorization code
            redirect_uri: Redirect URI

        Returns:
            Dict with access_token, refresh_token, expires_in, scopes
        """
        token = self._oauth.exchange_code(
            application_id, client_secret, code, redirect_uri
        )

        return {
            "access_token": token.access_token,
            "refresh_token": token.refresh_token,
            "token_type": "Bearer",
            "expires_in": token.expires_at - self._get_timestamp(),
            "scope": " ".join(token.scopes),
        }

    def refresh_token(
        self,
        application_id: SnowflakeID,
        client_secret: str,
        refresh_token: str,
    ) -> Dict[str, Any]:
        """
        Refresh an access token.

        Args:
            application_id: Application ID
            client_secret: Client secret
            refresh_token: Refresh token

        Returns:
            Dict with new tokens
        """
        token = self._oauth.refresh_token(application_id, client_secret, refresh_token)

        return {
            "access_token": token.access_token,
            "refresh_token": token.refresh_token,
            "token_type": "Bearer",
            "expires_in": token.expires_at - self._get_timestamp(),
            "scope": " ".join(token.scopes),
        }

    def revoke_token(self, token: str) -> bool:
        """
        Revoke an OAuth2 token.

        Args:
            token: Token to revoke

        Returns:
            True if revoked
        """
        return self._oauth.revoke_token(token)

    def register_command(
        self,
        application_id: SnowflakeID,
        name: str,
        description: str,
        command_type: CommandType = CommandType.CHAT_INPUT,
        server_id: Optional[SnowflakeID] = None,
        options: Optional[List[Dict[str, Any]]] = None,
        default_member_permissions: Optional[str] = None,
        dm_permission: bool = True,
        nsfw: bool = False,
    ) -> Command:
        """
        Register a new command.

        Args:
            application_id: Application ID
            name: Command name
            description: Command description
            command_type: Type of command
            server_id: Server ID for guild commands
            options: Command options
            default_member_permissions: Default permissions
            dm_permission: Whether command works in DMs
            nsfw: Whether command is NSFW

        Returns:
            Registered Command
        """
        return self._commands.register_command(
            application_id=application_id,
            name=name,
            description=description,
            command_type=command_type,
            server_id=server_id,
            options=options,
            default_member_permissions=default_member_permissions,
            dm_permission=dm_permission,
            nsfw=nsfw,
        )

    def update_command(
        self,
        command_id: SnowflakeID,
        name: Optional[str] = None,
        description: Optional[str] = None,
        options: Optional[List[Dict[str, Any]]] = None,
        default_member_permissions: Optional[str] = None,
        dm_permission: Optional[bool] = None,
        nsfw: Optional[bool] = None,
    ) -> Command:
        """Update a command."""
        return self._commands.update_command(
            command_id=command_id,
            name=name,
            description=description,
            options=options,
            default_member_permissions=default_member_permissions,
            dm_permission=dm_permission,
            nsfw=nsfw,
        )

    def delete_command(self, command_id: SnowflakeID) -> bool:
        """Delete a command."""
        return self._commands.delete_command(command_id)

    def get_commands(
        self,
        application_id: SnowflakeID,
        server_id: Optional[SnowflakeID] = None,
        include_global: bool = True,
    ) -> List[Command]:
        """Get commands for an application."""
        return self._commands.get_commands(application_id, server_id, include_global)

    def handle_interaction(
        self,
        application_id: SnowflakeID,
        interaction_type: InteractionType,
        user_id: SnowflakeID,
        data: Optional[Dict[str, Any]] = None,
        server_id: Optional[SnowflakeID] = None,
        channel_id: Optional[SnowflakeID] = None,
        message_id: Optional[SnowflakeID] = None,
        locale: Optional[str] = None,
        server_locale: Optional[str] = None,
    ) -> Interaction:
        """
        Create and handle an interaction.

        Args:
            application_id: Application ID
            interaction_type: Type of interaction
            user_id: User who triggered interaction
            data: Interaction data
            server_id: Server ID
            channel_id: Channel ID
            message_id: Message ID
            locale: User locale
            server_locale: Server locale

        Returns:
            Interaction with token
        """
        interaction = self._interactions.create_interaction(
            application_id=application_id,
            interaction_type=interaction_type,
            user_id=user_id,
            data=data,
            server_id=server_id,
            channel_id=channel_id,
            message_id=message_id,
            locale=locale,
            server_locale=server_locale,
        )

        self._interactions.dispatch_interaction(interaction)

        return interaction

    def create_interaction_response(
        self,
        interaction_token: str,
        response: InteractionResponse,
    ) -> bool:
        """
        Respond to an interaction.

        Args:
            interaction_token: Interaction token
            response: Response to send

        Returns:
            True if response sent
        """
        return self._interactions.respond(interaction_token, response)

    def verify_webhook_signature(
        self,
        body: bytes,
        signature: str,
        timestamp: str,
    ) -> bool:
        """
        Verify a webhook request signature.

        Args:
            body: Request body
            signature: X-Signature-Ed25519 header
            timestamp: X-Signature-Timestamp header

        Returns:
            True if signature is valid

        Raises:
            WebhookSignatureError: Invalid signature
        """
        secret = self._config.get("webhook_signature_secret", "")
        message = timestamp.encode() + body

        expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()

        if not hmac.compare_digest(expected, signature):
            raise WebhookSignatureError("Invalid webhook signature")

        return True

    def install_application(
        self,
        application_id: SnowflakeID,
        server_id: SnowflakeID,
        installer_id: SnowflakeID,
        permissions: str = "0",
        scopes: Optional[List[str]] = None,
    ) -> ApplicationInstallation:
        """
        Install an application on a server.

        Args:
            application_id: Application ID
            server_id: Server ID
            installer_id: User who installed
            permissions: Bot permissions
            scopes: Granted scopes

        Returns:
            ApplicationInstallation

        Raises:
            ApplicationNotFoundError: Application not found
            InstallationExistsError: Already installed
        """
        app = self.get_application(application_id)
        if not app:
            raise ApplicationNotFoundError("Application not found")

        existing = self._db.fetch_one(
            "SELECT id FROM app_installations WHERE application_id = ? AND server_id = ?",
            (application_id, server_id),
        )
        if existing:
            raise InstallationExistsError(
                "Application is already installed on this server"
            )

        installation_id = self._generate_id()
        now = self._get_timestamp()

        self._db.execute(
            """INSERT INTO app_installations
               (id, application_id, server_id, installer_id, permissions, scopes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                installation_id,
                application_id,
                server_id,
                installer_id,
                permissions,
                json.dumps(scopes or []),
                now,
                now,
            ),
        )

        if app.bot_id and self._servers:
            try:
                self._servers.add_member(server_id, app.bot_id)
            except Exception as e:
                logger.warning(f"Failed to add bot to server: {e}")

        logger.info(f"Application {application_id} installed on server {server_id}")

        return ApplicationInstallation(
            id=installation_id,
            application_id=application_id,
            server_id=server_id,
            installer_id=installer_id,
            permissions=permissions,
            scopes=scopes or [],
            created_at=now,
            updated_at=now,
        )

    def uninstall_application(
        self,
        application_id: SnowflakeID,
        server_id: SnowflakeID,
        user_id: SnowflakeID,
    ) -> bool:
        """
        Uninstall an application from a server.

        Args:
            application_id: Application ID
            server_id: Server ID
            user_id: User uninstalling

        Returns:
            True if uninstalled

        Raises:
            InstallationNotFoundError: Not installed
        """
        installation = self._db.fetch_one(
            "SELECT id FROM app_installations WHERE application_id = ? AND server_id = ?",
            (application_id, server_id),
        )
        if not installation:
            raise InstallationNotFoundError(
                "Application is not installed on this server"
            )

        self._db.execute(
            "DELETE FROM app_installations WHERE application_id = ? AND server_id = ?",
            (application_id, server_id),
        )

        app = self.get_application(application_id)
        if app and app.bot_id and self._servers:
            try:
                self._servers.remove_member(app.bot_id, server_id)
            except Exception as e:
                logger.warning(f"Failed to remove bot from server: {e}")

        logger.info(f"Application {application_id} uninstalled from server {server_id}")
        return True

    def get_installations(
        self,
        application_id: Optional[SnowflakeID] = None,
        server_id: Optional[SnowflakeID] = None,
    ) -> List[ApplicationInstallation]:
        """
        Get application installations.

        Args:
            application_id: Filter by application
            server_id: Filter by server

        Returns:
            List of ApplicationInstallation
        """
        if application_id and server_id:
            rows = self._db.fetch_all(
                """SELECT id, application_id, server_id, installer_id, permissions,
                          scopes, created_at, updated_at
                   FROM app_installations
                   WHERE application_id = ? AND server_id = ?""",
                (application_id, server_id),
            )
        elif application_id:
            rows = self._db.fetch_all(
                """SELECT id, application_id, server_id, installer_id, permissions,
                          scopes, created_at, updated_at
                   FROM app_installations WHERE application_id = ?""",
                (application_id,),
            )
        elif server_id:
            rows = self._db.fetch_all(
                """SELECT id, application_id, server_id, installer_id, permissions,
                          scopes, created_at, updated_at
                   FROM app_installations WHERE server_id = ?""",
                (server_id,),
            )
        else:
            rows = self._db.fetch_all(
                """SELECT id, application_id, server_id, installer_id, permissions,
                          scopes, created_at, updated_at
                   FROM app_installations"""
            )

        return [self._row_to_installation(row) for row in rows]

    def check_rate_limit(self, application_id: SnowflakeID) -> bool:
        """
        Check if an application is rate limited.

        Args:
            application_id: Application ID

        Returns:
            True if allowed, raises RateLimitError if limited
        """
        now = self._get_timestamp()
        limits = self._config.get("rate_limits", {})
        requests_per_minute = limits.get("requests_per_minute", 50)

        if application_id not in self._rate_limits:
            self._rate_limits[application_id] = {"count": 0, "reset_at": now + 60000}

        rate_info = self._rate_limits[application_id]

        if now >= rate_info["reset_at"]:
            rate_info["count"] = 0
            rate_info["reset_at"] = now + 60000

        if rate_info["count"] >= requests_per_minute:
            retry_after = (rate_info["reset_at"] - now) // 1000
            raise RateLimitError(
                f"Rate limit exceeded for application {application_id}", retry_after
            )

        rate_info["count"] += 1
        return True

    # === Bot Management Methods ===

    def _require_server_manage_permission(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
    ) -> None:
        """Require server.manage permission for bot administration actions."""
        if not self._servers:
            raise PermissionDeniedError("Servers module not available", "server.manage")
        if not self._servers.has_permission(user_id, server_id, "server.manage"):
            raise PermissionDeniedError(
                "Missing required permission: server.manage", "server.manage"
            )

    def approve_bot(
        self,
        server_id: SnowflakeID,
        application_id: SnowflakeID,
        approved_by: SnowflakeID,
        permissions: str = "0",
        bot_name: Optional[str] = None,
    ) -> ApprovedBot:
        """
        Approve a bot for installation on a server.

        Args:
            server_id: Server ID
            application_id: Application ID
            approved_by: Admin user ID who approved
            permissions: Bot permissions bitmask
            bot_name: Optional display name for the bot

        Returns:
            ApprovedBot

        Raises:
            BotAlreadyApprovedError: Bot already approved
            BotLimitError: Bot limit reached
            LicenseFeatureError: License does not allow bots
        """
        self._check_bot_license()
        self._require_server_manage_permission(approved_by, server_id)

        existing = self._db.fetch_one(
            "SELECT id FROM app_approved_bots WHERE server_id = ? AND application_id = ? AND status = 'approved'",
            (server_id, application_id),
        )
        if existing:
            raise BotAlreadyApprovedError("Bot is already approved on this server")

        bot_config = config.get("bots", {})
        max_bots = bot_config.get("max_per_server", 10)

        if self._check_premium_license():
            max_bots = bot_config.get("max_per_server_premium", 50)

        current_count = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM app_approved_bots WHERE server_id = ? AND status = 'approved'",
            (server_id,),
        )
        current = current_count["count"] if current_count else 0

        if current >= max_bots:
            raise BotLimitError(
                f"Maximum of {max_bots} approved bots per server", max_bots, current
            )

        bot_id = self._generate_id()
        now = self._get_timestamp()

        self._db.execute(
            """INSERT INTO app_approved_bots
               (id, server_id, application_id, approved_by, permissions, bot_name,
                bot_avatar_url, status, installed_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                bot_id,
                server_id,
                application_id,
                approved_by,
                permissions,
                bot_name,
                None,
                "approved",
                now,
                now,
            ),
        )

        # Auto-install the application on the server
        try:
            self.install_application(
                application_id, server_id, approved_by, permissions
            )
        except InstallationExistsError:
            pass
        except Exception as e:
            logger.warning(f"Failed to auto-install bot on server: {e}")

        logger.info(
            f"Bot {application_id} approved on server {server_id} by {approved_by}"
        )

        return ApprovedBot(
            id=bot_id,
            server_id=server_id,
            application_id=application_id,
            approved_by=approved_by,
            permissions=permissions,
            bot_name=bot_name,
            bot_avatar_url=None,
            status=BotApprovalStatus.APPROVED,
            installed_at=now,
            updated_at=now,
        )

    def remove_approved_bot(
        self,
        server_id: SnowflakeID,
        application_id: SnowflakeID,
        user_id: SnowflakeID,
    ) -> bool:
        """
        Remove an approved bot from a server.

        Args:
            server_id: Server ID
            application_id: Application ID
            user_id: User removing the bot

        Returns:
            True if removed
        """
        self._require_server_manage_permission(user_id, server_id)
        now = self._get_timestamp()

        self._db.execute(
            "UPDATE app_approved_bots SET status = 'removed', updated_at = ? WHERE server_id = ? AND application_id = ?",
            (now, server_id, application_id),
        )

        # Uninstall the application
        try:
            self.uninstall_application(application_id, server_id, user_id)
        except InstallationNotFoundError:
            pass
        except Exception as e:
            logger.warning(f"Failed to uninstall bot on removal: {e}")

        logger.info(f"Bot {application_id} removed from server {server_id}")
        return True

    def get_approved_bots(
        self,
        server_id: Optional[SnowflakeID] = None,
        application_id: Optional[SnowflakeID] = None,
        status: Optional[str] = None,
    ) -> List[ApprovedBot]:
        """
        Get approved bots with optional filters.

        Args:
            server_id: Filter by server
            application_id: Filter by application
            status: Filter by status (pending, approved, denied, removed)

        Returns:
            List of ApprovedBot
        """
        conditions = []
        params = []

        if server_id:
            conditions.append("ab.server_id = ?")
            params.append(server_id)
        if application_id:
            conditions.append("ab.application_id = ?")
            params.append(application_id)
        if status:
            conditions.append("ab.status = ?")
            params.append(status)

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

        rows = self._db.fetch_all(
            f"""SELECT ab.id, ab.server_id, ab.application_id, ab.approved_by,
                      ab.permissions, ab.bot_name, ab.bot_avatar_url, ab.status,
                      ab.installed_at, ab.updated_at,
                      a.name as app_name, a.icon_url as app_icon, a.bot_id
               FROM app_approved_bots ab
               LEFT JOIN app_applications a ON ab.application_id = a.id{where_clause}
               ORDER BY ab.installed_at DESC""",
            tuple(params),
        )

        return [self._row_to_approved_bot(row) for row in rows]

    def request_bot(
        self,
        server_id: SnowflakeID,
        application_id: SnowflakeID,
        requester_id: SnowflakeID,
        reason: Optional[str] = None,
    ) -> BotRequest:
        """
        Request approval for a bot on a server.

        Args:
            server_id: Server ID
            application_id: Application ID
            requester_id: User requesting the bot
            reason: Optional reason for the request

        Returns:
            BotRequest

        Raises:
            BotRequestExistsError: Pending request already exists
            BotAlreadyApprovedError: Bot already approved
        """
        existing = self._db.fetch_one(
            "SELECT id FROM app_bot_requests WHERE server_id = ? AND application_id = ? AND status = 'pending'",
            (server_id, application_id),
        )
        if existing:
            raise BotRequestExistsError("A pending request for this bot already exists")

        already_approved = self._db.fetch_one(
            "SELECT id FROM app_approved_bots WHERE server_id = ? AND application_id = ? AND status = 'approved'",
            (server_id, application_id),
        )
        if already_approved:
            raise BotAlreadyApprovedError("Bot is already approved on this server")

        request_id = self._generate_id()
        now = self._get_timestamp()

        self._db.execute(
            """INSERT INTO app_bot_requests
               (id, server_id, application_id, requester_id, reason, status,
                reviewed_by, review_reason, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'pending', NULL, NULL, ?, ?)""",
            (request_id, server_id, application_id, requester_id, reason, now, now),
        )

        logger.info(
            f"Bot request {request_id} for app {application_id} on server {server_id}"
        )

        return BotRequest(
            id=request_id,
            server_id=server_id,
            application_id=application_id,
            requester_id=requester_id,
            reason=reason,
            status=BotApprovalStatus.PENDING,
            reviewed_by=None,
            review_reason=None,
            created_at=now,
            updated_at=now,
        )

    def review_bot_request(
        self,
        server_id: Optional[SnowflakeID],
        request_id: SnowflakeID,
        reviewer_id: SnowflakeID,
        approve: bool,
        review_reason: Optional[str] = None,
    ) -> BotRequest:
        """
        Review a bot request.

        Args:
            request_id: Request ID
            reviewer_id: Admin user ID
            approve: Whether to approve
            review_reason: Optional review comment

        Returns:
            Updated BotRequest
        """
        row = self._db.fetch_one(
            "SELECT * FROM app_bot_requests WHERE id = ?",
            (request_id,),
        )
        if not row:
            raise BotRequestError("Bot request not found", request_id)

        if server_id is not None and row["server_id"] != server_id:
            raise BotRequestError("Bot request not found", request_id)

        self._require_server_manage_permission(reviewer_id, row["server_id"])

        status = "approved" if approve else "denied"
        now = self._get_timestamp()

        self._db.execute(
            """UPDATE app_bot_requests SET status = ?, reviewed_by = ?, review_reason = ?,
               updated_at = ? WHERE id = ?""",
            (status, reviewer_id, review_reason, now, request_id),
        )

        # If approved, auto-approve the bot
        if approve:
            try:
                self.approve_bot(
                    server_id=row["server_id"],
                    application_id=row["application_id"],
                    approved_by=reviewer_id,
                )
            except BotAlreadyApprovedError:
                pass
            except Exception as e:
                logger.warning(f"Failed to auto-approve bot after request review: {e}")

        logger.info(f"Bot request {request_id} {status} by {reviewer_id}")

        return BotRequest(
            id=row["id"],
            server_id=row["server_id"],
            application_id=row["application_id"],
            requester_id=row["requester_id"],
            reason=row["reason"],
            status=BotApprovalStatus(status),
            reviewed_by=reviewer_id,
            review_reason=review_reason,
            created_at=row["created_at"],
            updated_at=now,
        )

    def get_bot_requests(
        self,
        server_id: Optional[SnowflakeID] = None,
        requester_id: Optional[SnowflakeID] = None,
        status: Optional[str] = None,
    ) -> List[BotRequest]:
        """
        Get bot requests with optional filters.

        Args:
            server_id: Filter by server
            requester_id: Filter by requester
            status: Filter by status

        Returns:
            List of BotRequest
        """
        conditions = []
        params = []

        if server_id:
            conditions.append("server_id = ?")
            params.append(server_id)
        if requester_id:
            conditions.append("requester_id = ?")
            params.append(requester_id)
        if status:
            conditions.append("status = ?")
            params.append(status)

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

        rows = self._db.fetch_all(
            f"""SELECT id, server_id, application_id, requester_id, reason, status,
                      reviewed_by, review_reason, created_at, updated_at
               FROM app_bot_requests{where_clause}
               ORDER BY created_at DESC""",
            tuple(params),
        )

        return [self._row_to_bot_request(row) for row in rows]

    def get_bot_profile(self, application_id: SnowflakeID) -> Optional[BotProfile]:
        """
        Get a bot's public profile.

        Args:
            application_id: Application ID

        Returns:
            BotProfile or None
        """
        row = self._db.fetch_one(
            """SELECT application_id, description, short_description, avatar_url, banner_url,
                      website_url, support_url, github_url, tags, nsfw, private, updated_at
               FROM app_bot_profiles WHERE application_id = ?""",
            (application_id,),
        )

        if not row:
            return None

        return self._row_to_bot_profile(row)

    def update_bot_profile(
        self,
        application_id: SnowflakeID,
        user_id: SnowflakeID,
        description: Optional[str] = None,
        short_description: Optional[str] = None,
        avatar_url: Optional[str] = None,
        banner_url: Optional[str] = None,
        website_url: Optional[str] = None,
        support_url: Optional[str] = None,
        github_url: Optional[str] = None,
        tags: Optional[List[str]] = None,
        nsfw: Optional[bool] = None,
        private: Optional[bool] = None,
    ) -> BotProfile:
        """
        Update a bot's public profile.

        Args:
            application_id: Application ID
            user_id: User making the update

        Returns:
            Updated BotProfile
        """
        app = self.get_application(application_id)
        if not app:
            raise ApplicationNotFoundError("Application not found")
        if app.owner_id != user_id:
            raise ApplicationAccessDeniedError("You do not own this application")

        existing = self.get_bot_profile(application_id)
        now = self._get_timestamp()

        if existing:
            updates = []
            params = []
            if description is not None:
                updates.append("description = ?")
                params.append(description)
            if short_description is not None:
                updates.append("short_description = ?")
                params.append(short_description)
            if avatar_url is not None:
                updates.append("avatar_url = ?")
                params.append(avatar_url)
            if banner_url is not None:
                updates.append("banner_url = ?")
                params.append(banner_url)
            if website_url is not None:
                updates.append("website_url = ?")
                params.append(website_url)
            if support_url is not None:
                updates.append("support_url = ?")
                params.append(support_url)
            if github_url is not None:
                updates.append("github_url = ?")
                params.append(github_url)
            if tags is not None:
                updates.append("tags = ?")
                params.append(json.dumps(tags))
            if nsfw is not None:
                updates.append("nsfw = ?")
                params.append(1 if nsfw else 0)
            if private is not None:
                updates.append("private = ?")
                params.append(1 if private else 0)

            if updates:
                updates.append("updated_at = ?")
                params.append(now)
                params.append(application_id)
                self._db.execute(
                    f"UPDATE app_bot_profiles SET {', '.join(updates)} WHERE application_id = ?",
                    tuple(params),
                )
        else:
            self._db.execute(
                """INSERT INTO app_bot_profiles
                   (application_id, description, short_description, avatar_url, banner_url,
                    website_url, support_url, github_url, tags, nsfw, private, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    application_id,
                    description,
                    short_description,
                    avatar_url,
                    banner_url,
                    website_url,
                    support_url,
                    github_url,
                    json.dumps(tags or []),
                    1 if nsfw else 0,
                    1 if private else 0,
                    now,
                ),
            )

        logger.info(f"Bot profile updated for application {application_id}")

        result = self.get_bot_profile(application_id)
        assert result is not None
        return result

    def get_user_authorized_apps(
        self, user_id: SnowflakeID
    ) -> List[UserAuthorizedApplication]:
        """
        Get all OAuth2 applications authorized by a user.

        Args:
            user_id: User ID

        Returns:
            List of UserAuthorizedApplication
        """
        rows = self._db.fetch_all(
            """SELECT t.id, t.application_id, t.scopes, t.created_at, t.expires_at,
                      a.name as app_name, a.icon_url as app_icon
               FROM app_oauth_tokens t
               JOIN app_applications a ON t.application_id = a.id
               WHERE t.user_id = ? AND t.revoked = 0
               ORDER BY t.created_at DESC""",
            (user_id,),
        )

        return [
            UserAuthorizedApplication(
                id=row["id"],
                application_id=row["application_id"],
                application_name=row["app_name"],
                application_icon=row["app_icon"],
                scopes=json.loads(row["scopes"]) if row["scopes"] else [],
                authorized_at=row["created_at"],
                last_used_at=row["expires_at"],
            )
            for row in rows
        ]

    def revoke_authorized_app(
        self, token_id: SnowflakeID, user_id: SnowflakeID
    ) -> bool:
        """
        Revoke an authorized application.

        Args:
            token_id: Token ID
            user_id: User ID

        Returns:
            True if revoked
        """
        self._db.execute(
            "UPDATE app_oauth2_tokens SET revoked = 1 WHERE id = ? AND user_id = ?",
            (token_id, user_id),
        )
        logger.info(f"Authorized app token {token_id} revoked by user {user_id}")
        return True

    def get_bot_directory(
        self,
        server_id: Optional[SnowflakeID] = None,
        include_public: bool = True,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Get a directory of available bots for a server.

        Args:
            server_id: Filter bots not already installed on this server
            include_public: Whether to include public bots
            limit: Page size
            offset: Page offset

        Returns:
            List of bot directory entries with app info and install status
        """
        query_parts = [
            """SELECT a.id, a.name, a.description, a.icon_url, a.bot_id,
                      bp.short_description, bp.tags, bp.nsfw, bp.private
               FROM app_applications a
               LEFT JOIN app_bot_profiles bp ON a.id = bp.application_id
               WHERE a.bot_id IS NOT NULL"""
        ]
        params = []

        if include_public:
            query_parts.append("AND (bp.private IS NULL OR bp.private = 0)")

        if server_id:
            query_parts.append(
                "AND a.id NOT IN (SELECT application_id FROM app_approved_bots WHERE server_id = ? AND status = 'approved')"
            )
            params.append(server_id)

        query_parts.append("ORDER BY a.name ASC")
        query_parts.append("LIMIT ? OFFSET ?")
        params.extend([limit, offset])

        rows = self._db.fetch_all(" ".join(query_parts), tuple(params))

        result = []
        for row in rows:
            tags = json.loads(row["tags"]) if row["tags"] else []
            result.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "description": row["short_description"] or row["description"],
                    "icon_url": row["icon_url"],
                    "bot_id": row["bot_id"],
                    "tags": tags,
                    "nsfw": bool(row["nsfw"]) if row["nsfw"] else False,
                }
            )

        return result

    @dataclass
    class AdminBotStats:
        total_applications: int = 0
        total_bots: int = 0
        total_approved: int = 0
        total_pending_requests: int = 0
        total_installations: int = 0
        servers_with_bots: int = 0
        recent_approvals: int = 0
        recent_requests: int = 0

    def _count_rows(self, table: str, where: str = "", params: tuple = ()) -> int:
        if not self._db.table_exists(table):
            return 0
        query = f"SELECT COUNT(*) as count FROM {table}"
        if where:
            query = f"{query} WHERE {where}"
        row = self._db.fetch_one(query, params)
        return int(row["count"]) if row else 0

    def _maybe_count_rows(self, query: str, params: tuple = ()) -> int:
        try:
            row = self._db.fetch_one(query, params)
            return int(row["count"]) if row else 0
        except Exception:
            return 0

    def _count_rows_any(
        self, tables: List[str], where: str = "", params: tuple = ()
    ) -> int:
        for table in tables:
            if self._db.table_exists(table):
                return self._count_rows(table, where, params)
        return 0

    def _table_count_query(
        self, table: str, where: str = "", params: tuple = ()
    ) -> int:
        if not self._db.table_exists(table):
            return 0
        return self._maybe_count_rows(
            f"SELECT COUNT(*) as count FROM {table}"
            + (f" WHERE {where}" if where else ""),
            params,
        )

    def get_admin_bot_stats(self) -> Dict[str, int]:
        """Get bot statistics for the admin dashboard using canonical schema names."""
        week_ago = self._get_timestamp() - 604800
        return {
            "total_applications": self._count_rows("app_applications"),
            "total_bots": self._count_rows("app_applications", "bot_id IS NOT NULL"),
            "total_approved": self._count_rows(
                "app_approved_bots", "status = 'approved'"
            ),
            "total_pending_requests": self._count_rows(
                "app_bot_requests", "status = 'pending'"
            ),
            "total_installations": self._count_rows("app_installations"),
            "servers_with_bots": self._maybe_count_rows(
                "SELECT COUNT(DISTINCT server_id) as count FROM app_approved_bots WHERE status = 'approved'"
            ),
            "recent_approvals": self._count_rows(
                "app_approved_bots", "installed_at >= ?", (week_ago,)
            ),
            "recent_requests": self._count_rows(
                "app_bot_requests", "created_at >= ?", (week_ago,)
            ),
        }

    def get_admin_dashboard_feature_stats(self) -> Dict[str, Any]:
        """Get dashboard feature stats without assuming legacy table names."""
        feature_stats: Dict[str, Any] = {}
        feature_stats["bookmarks"] = self._count_rows("user_bookmarks")
        feature_stats["scheduled_messages_pending"] = self._count_rows_any(
            ["msg_scheduled", "scheduled_messages"], "status = 'pending'"
        )
        feature_stats["forwarded_messages"] = self._count_rows_any(
            ["msg_forwarded", "forwarded_messages"]
        )
        feature_stats["voice_messages"] = self._maybe_count_rows(
            "SELECT COUNT(*) as count FROM msg_messages WHERE message_type = 'voice'"
        )
        feature_stats["profiles_with_status"] = self._count_rows(
            "user_profiles", "custom_status_text IS NOT NULL"
        )
        feature_stats["push_tokens"] = self._count_rows("push_tokens")
        feature_stats["webhook_retries_pending"] = self._count_rows(
            "webhook_retry_queue", "status = 'pending'"
        )
        if self._db.table_exists("message_reports"):
            rows = self._db.fetch_all(
                "SELECT category, COUNT(*) as count FROM message_reports GROUP BY category ORDER BY count DESC LIMIT 10"
            )
            feature_stats["report_categories"] = [
                {"category": row["category"], "count": row["count"]} for row in rows
            ]
        else:
            feature_stats["report_categories"] = []
        feature_stats["dm_spam_filters_active"] = self._count_rows(
            "dm_spam_filters", "enabled = 1"
        )
        feature_stats["threads_with_slowmode"] = self._count_rows(
            "thread_threads", "slowmode_interval_ms > 0"
        )
        return feature_stats

    def get_admin_dashboard_counts(self) -> Dict[str, Any]:
        """Get top-line dashboard counts without route-level SQL."""
        total_users = self._count_rows("auth_users")
        active_users = self._count_rows(
            "auth_users", "last_login_at > ?", (int(self._get_timestamp() - 86400000),)
        )
        scheduled_deletions = self._count_rows(
            "auth_users", "deletion_status = 'frozen'"
        )
        return {
            "total_users": total_users,
            "active_users": active_users,
            "scheduled_deletions": scheduled_deletions,
            "db_status": "healthy",
        }

    def get_admin_bot_applications(
        self, limit: int = 50, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Return applications with aggregated bot stats for admin views."""
        if limit > 100:
            limit = 100

        rows = self._db.fetch_all(
            """SELECT a.id, a.name, a.owner_id, a.bot_id, a.icon_url, a.created_at,
                      (SELECT COUNT(*) FROM app_approved_bots ab
                       WHERE ab.application_id = a.id AND ab.status = 'approved') AS approved_count,
                      (SELECT COUNT(*) FROM app_bot_requests br
                       WHERE br.application_id = a.id AND br.status = 'pending') AS pending_count
               FROM app_applications a
               ORDER BY a.created_at DESC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        )
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "owner_id": row["owner_id"],
                "bot_id": row["bot_id"],
                "icon_url": row["icon_url"],
                "approved_servers": row["approved_count"] or 0,
                "pending_requests": row["pending_count"] or 0,
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_admin_bot_requests(
        self,
        status_filter: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Return bot approval requests for admin views."""
        if limit > 100:
            limit = 100

        conditions = []
        params: List[Any] = []
        if status_filter:
            conditions.append("br.status = ?")
            params.append(status_filter)

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
        params.extend([limit, offset])

        rows = self._db.fetch_all(
            f"""SELECT br.id, br.application_id, a.name as app_name, br.server_id,
                      br.requester_id, br.reason, br.status, br.created_at
               FROM app_bot_requests br
               JOIN app_applications a ON br.application_id = a.id{where_clause}
               ORDER BY br.created_at DESC
               LIMIT ? OFFSET ?""",
            tuple(params),
        )
        return [
            {
                "id": row["id"],
                "application_id": row["application_id"],
                "application_name": row["app_name"],
                "server_id": row["server_id"],
                "requester_id": row["requester_id"],
                "reason": row["reason"],
                "status": row["status"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def _check_bot_license(self) -> bool:
        """
        Check if bots feature is enabled by license.

        Returns:
            True if allowed

        Raises:
            LicenseFeatureError: Feature not licensed
        """
        try:
            from utils import licensing as license_module  # type: ignore[import]

            if hasattr(license_module, "setup"):
                if not license_module.has_feature("bots", default=False):
                    raise LicenseFeatureError(
                        "Bots feature requires a valid license", "bots"
                    )
            return True
        except ImportError:
            logger.warning("Licensing module not available, bots feature allowed")
            return True
        except Exception as e:
            logger.warning(f"License check failed for bots feature: {e}")
            return True

    def _check_premium_license(self) -> bool:
        """
        Check if premium license is active.

        Returns:
            True if premium features allowed
        """
        try:
            from utils import licensing as license_module  # type: ignore[import]

            if hasattr(license_module, "has_feature"):
                return license_module.has_feature("premium", default=False)
            return False
        except ImportError:
            return False
        except Exception:
            return False

    def _row_to_application(self, row) -> Application:
        """Convert database row to Application."""
        redirect_uris = json.loads(row["redirect_uris"]) if row["redirect_uris"] else []

        return Application(
            id=row["id"],
            owner_id=row["owner_id"],
            name=row["name"],
            description=row["description"],
            icon_url=row["icon_url"],
            bot_id=row["bot_id"],
            bot_public=bool(row["bot_public"]),
            bot_require_code_grant=bool(row["bot_require_code_grant"]),
            terms_of_service_url=row["terms_of_service_url"],
            privacy_policy_url=row["privacy_policy_url"],
            redirect_uris=redirect_uris,
            interactions_endpoint_url=row["interactions_endpoint_url"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_installation(self, row) -> ApplicationInstallation:
        """Convert database row to ApplicationInstallation."""
        scopes = json.loads(row["scopes"]) if row["scopes"] else []

        return ApplicationInstallation(
            id=row["id"],
            application_id=row["application_id"],
            server_id=row["server_id"],
            installer_id=row["installer_id"],
            permissions=row["permissions"],
            scopes=scopes,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_approved_bot(self, row) -> ApprovedBot:
        """Convert database row to ApprovedBot."""
        return ApprovedBot(
            id=row["id"],
            server_id=row["server_id"],
            application_id=row["application_id"],
            approved_by=row["approved_by"],
            permissions=row["permissions"],
            bot_name=row["bot_name"],
            bot_avatar_url=row["bot_avatar_url"],
            status=BotApprovalStatus(row["status"]),
            installed_at=row["installed_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_bot_request(self, row) -> BotRequest:
        """Convert database row to BotRequest."""
        return BotRequest(
            id=row["id"],
            server_id=row["server_id"],
            application_id=row["application_id"],
            requester_id=row["requester_id"],
            reason=row["reason"],
            status=BotApprovalStatus(row["status"]),
            reviewed_by=row["reviewed_by"],
            review_reason=row["review_reason"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_bot_profile(self, row) -> BotProfile:
        """Convert database row to BotProfile."""
        tags = json.loads(row["tags"]) if row["tags"] else []

        return BotProfile(
            application_id=row["application_id"],
            description=row["description"],
            short_description=row["short_description"],
            avatar_url=row["avatar_url"],
            banner_url=row["banner_url"],
            website_url=row["website_url"],
            support_url=row["support_url"],
            github_url=row["github_url"],
            tags=tags,
            nsfw=bool(row["nsfw"]) if row["nsfw"] else False,
            private=bool(row["private"]) if row["private"] else False,
            updated_at=row["updated_at"],
        )
