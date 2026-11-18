import json
import threading
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
from ..oauth.scopes import VALID_SCOPES, require_allowed_scopes
from ..validation import (
    validate_application_description,
    validate_application_name,
    validate_homepage_url,
    validate_redirect_uris,
    validate_webhook_url,
)
from src.core.security.hardening import (
    HardeningViolation,
    bounded_int,
    reject_control_characters,
)
from .row_mappers import row_to_application
from .protocol import ApplicationManagerProtocol
from .bot_management import invalidate_bot_directory_cache
from .lookup_cache import (
    CACHE_MISS,
    BoundedTTLCache,
    application_byte_size,
    copy_application,
)


# Application metadata is read on several hot per-request paths (OAuth,
# interactions, installations, bot profiles) and the same row is often resolved
# more than once. A short-lived, bounded per-manager cache collapses those
# repeats into a single DB read; writes to ``app_applications`` invalidate the
# affected id explicitly (see ``_invalidate_app_meta_cache`` call sites).
_APP_META_CACHE_TTL_SECONDS = 2.0
_APP_META_CACHE_MAX_ENTRIES = 128
_APP_META_CACHE_MAX_BYTES = 256 * 1024
_APP_CACHE_INIT_LOCK = threading.Lock()


def validate_allowed_scopes_for_app(value: Optional[List[str]]) -> List[str]:
    """Default a new application to all known scopes or validate the ceiling."""
    if value is None:
        return sorted(VALID_SCOPES)
    return require_allowed_scopes(value)


class AppCRUDMixin(ApplicationManagerProtocol):
    def _get_app_meta_cache(self) -> BoundedTTLCache:
        """Return this manager's metadata cache, creating it on first use.

        The cache lives on the manager (not at module scope) so separate
        managers/DB handles never share entries.
        """
        cache = getattr(self, "_app_meta_cache", None)
        if cache is None:
            with _APP_CACHE_INIT_LOCK:
                cache = getattr(self, "_app_meta_cache", None)
                if cache is None:
                    cache = BoundedTTLCache(
                        max_entries=_APP_META_CACHE_MAX_ENTRIES,
                        max_bytes=_APP_META_CACHE_MAX_BYTES,
                        ttl_seconds=_APP_META_CACHE_TTL_SECONDS,
                        sizer=application_byte_size,
                    )
                    self._app_meta_cache = cache
        return cache

    def _invalidate_app_meta_cache(
        self, application_id: Optional[SnowflakeID] = None
    ) -> None:
        """Drop cached metadata for ``application_id`` after a write."""
        self._get_app_meta_cache().invalidate(application_id)

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
        allowed_scopes: Optional[List[str]] = None,
    ) -> Application:
        name = self._validate_app_name(name)
        redirect_uris = validate_redirect_uris(redirect_uris)
        allowed_scopes = validate_allowed_scopes_for_app(allowed_scopes)
        description = validate_application_description(description)
        terms_of_service_url = validate_homepage_url(
            terms_of_service_url, field="terms_of_service_url"
        )
        privacy_policy_url = validate_homepage_url(
            privacy_policy_url, field="privacy_policy_url"
        )
        interactions_endpoint_url = validate_webhook_url(
            interactions_endpoint_url, field="interactions_endpoint_url"
        )

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

        uris_json = json.dumps(redirect_uris)
        scopes_json = json.dumps(allowed_scopes)

        self._db.execute(
            """INSERT INTO app_applications
               (id, owner_id, name, description, icon_url, bot_id, bot_public,
                bot_require_code_grant, terms_of_service_url, privacy_policy_url,
                redirect_uris, allowed_scopes, interactions_endpoint_url,
                client_secret_hash, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                scopes_json,
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
            redirect_uris=redirect_uris,
            interactions_endpoint_url=interactions_endpoint_url,
            created_at=now,
            updated_at=now,
            client_secret=client_secret,
            client_secret_hash=secret_hash,
            allowed_scopes=allowed_scopes,
        )

    def get_application(
        self, application_id: SnowflakeID, user_id: Optional[SnowflakeID] = None
    ) -> Optional[Application]:
        cache = self._get_app_meta_cache()
        app = cache.get(application_id)
        if app is CACHE_MISS:
            row = self._db.fetch_one(
                """SELECT id, owner_id, name, description, icon_url, bot_id, bot_public,
                       bot_require_code_grant, terms_of_service_url, privacy_policy_url,
                       redirect_uris, allowed_scopes, interactions_endpoint_url,
                       created_at, updated_at
                FROM app_applications WHERE id = ?""",
                (application_id,),
            )

            if not row:
                # Do not cache misses: a just-created application must be
                # visible immediately without waiting out the TTL.
                return None

            app = row_to_application(row)
            cache.put(application_id, app)

        # SECURITY: when a caller context is supplied, enforce ownership at
        # the core layer so route-level gaps cannot leak application
        # metadata (name, redirect URIs, bot ID, timestamps) to other users.
        # Internal callers omit ``user_id`` and keep unrestricted access.
        # This check runs on cache hits too: caching the shared row never
        # bypasses authorization, which stays a per-caller decision.
        if user_id is not None and app.owner_id != user_id:
            raise ApplicationAccessDeniedError("You do not own this application")

        # Hand out an independent copy: callers must not be able to mutate the
        # cached snapshot (redirect_uris / allowed_scopes are mutable lists).
        return copy_application(app)

    def get_user_applications(
        self,
        user_id: SnowflakeID,
        *,
        limit: int = 100,
        offset: int = 0,
        name_filter: Optional[str] = None,
        parse_redirect_uris: bool = True,
    ) -> List[Application]:
        """Return a bounded, optionally name-filtered page of applications."""
        from ..validation import MAX_APPLICATION_FILTER_LENGTH

        bounded_limit = bounded_int(limit, "limit", minimum=1, maximum=100)
        bounded_offset = bounded_int(offset, "offset", minimum=0, maximum=100_000)

        where = ""
        params: List[Any] = [user_id]
        if name_filter is not None:
            text = reject_control_characters(name_filter, "name_filter").strip()
            if not text or len(text) > MAX_APPLICATION_FILTER_LENGTH:
                raise HardeningViolation("name_filter is outside the allowed length")
            # Escape LIKE wildcards so a filter string cannot broaden the match.
            escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where = " AND name LIKE ? ESCAPE '\\'"
            params.append(f"%{escaped}%")
        params.extend([bounded_limit, bounded_offset])

        rows = self._db.fetch_all(
            """SELECT id, owner_id, name, description, icon_url, bot_id, bot_public,
                      bot_require_code_grant, terms_of_service_url, privacy_policy_url,
                      redirect_uris, interactions_endpoint_url, created_at, updated_at
               FROM app_applications WHERE owner_id = ?"""
            + where
            + " ORDER BY created_at DESC LIMIT ? OFFSET ?",
            tuple(params),
        )

        return [
            row_to_application(row, parse_redirect_uris=parse_redirect_uris)
            for row in rows
        ]

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
        allowed_scopes: Optional[List[str]] = None,
    ) -> Application:
        app = self.get_application(application_id)
        if not app:
            raise ApplicationNotFoundError("Application not found")

        if app.owner_id != user_id:
            raise ApplicationAccessDeniedError("You do not own this application")

        if redirect_uris is not None:
            redirect_uris = validate_redirect_uris(redirect_uris)
        if allowed_scopes is not None:
            allowed_scopes = validate_allowed_scopes_for_app(allowed_scopes)
        description = validate_application_description(description)
        terms_of_service_url = validate_homepage_url(
            terms_of_service_url, field="terms_of_service_url"
        )
        privacy_policy_url = validate_homepage_url(
            privacy_policy_url, field="privacy_policy_url"
        )
        interactions_endpoint_url = validate_webhook_url(
            interactions_endpoint_url, field="interactions_endpoint_url"
        )

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

        if allowed_scopes is not None:
            updates.append("allowed_scopes = ?")
            params.append(json.dumps(allowed_scopes))

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
            # Name/icon are surfaced in bot directory entries.
            invalidate_bot_directory_cache()
            # The cached row carries the updated columns; drop it so the read
            # below (and every later caller) observes the committed write.
            self._invalidate_app_meta_cache(application_id)

        result = self.get_application(application_id)
        if result is None:
            raise ApplicationNotFoundError("Application not found after update")
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

        if app.bot_id and self._auth:
            try:
                self._auth.delete_bot(owner_id=user_id, bot_id=app.bot_id)
            except Exception as e:
                logger.warning(f"Failed to delete bot on app deletion: {e}")

        self._db.execute("DELETE FROM app_applications WHERE id = ?", (application_id,))
        logger.info(f"Application deleted: {application_id}")
        invalidate_bot_directory_cache()
        self._invalidate_app_meta_cache(application_id)
        return True

    def _validate_app_name(self, name: str) -> str:
        # Delegate to the shared rule so control characters are rejected too,
        # but keep the domain exception existing callers already handle.
        try:
            return validate_application_name(name)
        except HardeningViolation as exc:
            raise InvalidApplicationNameError(str(exc)) from exc

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
        # updated_at is part of the cached metadata snapshot.
        self._invalidate_app_meta_cache(application_id)
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

        base_username = f"{app.name.lower().replace(' ', '_')}_bot"
        bot_username = base_username
        for attempt in range(100):
            taken = self._db.fetch_one(
                "SELECT 1 FROM auth_users WHERE username = ? UNION SELECT 1 FROM auth_bots WHERE username = ?",
                (bot_username, bot_username),
            )
            if not taken:
                break
            bot_username = f"{base_username}_{attempt + 2}"
        else:
            raise ApplicationAccessDeniedError(
                "Could not generate unique bot username, please rename the application"
            )
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
        # Setting bot_id is what makes an application appear in the directory.
        invalidate_bot_directory_cache()
        # bot_id is part of the cached metadata snapshot.
        self._invalidate_app_meta_cache(application_id)

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
        code_challenge: Optional[str] = None,
        code_challenge_method: Optional[str] = None,
    ) -> str:
        return self._oauth.generate_authorization_url(
            application_id,
            redirect_uri,
            scopes,
            state,
            permissions,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
        )

    def exchange_code(
        self,
        application_id: SnowflakeID,
        client_secret: str,
        code: str,
        redirect_uri: str,
        code_verifier: Optional[str] = None,
    ) -> Dict[str, Any]:
        token = self._oauth.exchange_code(
            application_id, client_secret, code, redirect_uri, code_verifier
        )

        return {
            "access_token": token.access_token,
            "refresh_token": token.refresh_token,
            "token_type": "Bearer",
            "expires_in": (token.expires_at - self._get_timestamp()) // 1000,
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
            "expires_in": (token.expires_at - self._get_timestamp()) // 1000,
            "scope": " ".join(token.scopes),
        }

    def revoke_token(self, token: str, user_id: Optional[SnowflakeID] = None) -> bool:
        return self._oauth.revoke_token(token, user_id)

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
        application_id: Optional[SnowflakeID] = None,
    ) -> bool:
        return self._interactions.respond(
            interaction_token, response, application_id=application_id
        )
