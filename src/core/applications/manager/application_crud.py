import json
from typing import Optional, List, Dict, Any

import utils.logger as logger
from src.core.base import SnowflakeID

from ..models import (
    Application,
    Command,
    Interaction,
    InteractionResponse,
    CommandType,
    InteractionType,
)
from ..exceptions import (
    ApplicationNotFoundError,
    ApplicationAccessDeniedError,
    ApplicationLimitError,
    InvalidApplicationNameError,
)
from ..oauth.tokens import generate_client_secret
from .row_mappers import row_to_application
from .protocol import ApplicationManagerProtocol


class AppCRUDMixin(ApplicationManagerProtocol):
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
        row = self._db.fetch_one(
            """SELECT id, owner_id, name, description, icon_url, bot_id, bot_public,
                      bot_require_code_grant, terms_of_service_url, privacy_policy_url,
                      redirect_uris, interactions_endpoint_url, created_at, updated_at
               FROM app_applications WHERE id = ?""",
            (application_id,),
        )

        if not row:
            return None

        return row_to_application(row)

    def get_user_applications(self, user_id: SnowflakeID) -> List[Application]:
        rows = self._db.fetch_all(
            """SELECT id, owner_id, name, description, icon_url, bot_id, bot_public,
                      bot_require_code_grant, terms_of_service_url, privacy_policy_url,
                      redirect_uris, interactions_endpoint_url, created_at, updated_at
               FROM app_applications WHERE owner_id = ?
               ORDER BY created_at DESC""",
            (user_id,),
        )

        return [row_to_application(row) for row in rows]

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
        assert result is not None
        return result

    def delete_application(
        self, user_id: SnowflakeID, application_id: SnowflakeID
    ) -> bool:
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
        token = self._oauth.refresh_token(application_id, client_secret, refresh_token)

        return {
            "access_token": token.access_token,
            "refresh_token": token.refresh_token,
            "token_type": "Bearer",
            "expires_in": token.expires_at - self._get_timestamp(),
            "scope": " ".join(token.scopes),
        }

    def revoke_token(self, token: str) -> bool:
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
        return self._commands.delete_command(command_id)

    def get_commands(
        self,
        application_id: SnowflakeID,
        server_id: Optional[SnowflakeID] = None,
        include_global: bool = True,
    ) -> List[Command]:
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
        return self._interactions.respond(interaction_token, response)
