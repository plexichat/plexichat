"""
Application Manager - Core business logic for application operations.

Handles creating, updating, and managing applications with proper
validation, permission checks, and database interactions.
"""

import json
import hmac
import hashlib
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
                f"UPDATE app_applications SET {', '.join(updates)} WHERE id = ?",  # nosec: B608
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
