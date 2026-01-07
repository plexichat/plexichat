"""
Applications module - Zero-friction API for bot applications.

Provides OAuth2 authorization, application management, slash commands,
interactions, and component builders.

Setup once in main.py, use anywhere via import.

Usage:
    # In main.py (setup once)
    from src.core import applications
    applications.setup(db, auth, servers, events)

    # In any other file (use directly)
    from src.core import applications
    app = applications.create_application(user_id=1, name="My Bot")
"""

from typing import Optional, List, Dict, Any

from .models import (
    Application,
    Command,
    CommandOption,
    CommandChoice,
    Interaction,
    InteractionData,
    InteractionResponse,
    ApplicationInstallation,
    OAuth2Token,
    OAuth2AuthorizationCode,
    Button,
    SelectMenu,
    SelectOption,
    TextInput,
    ActionRow,
    Modal,
    WebhookDelivery,
    ApplicationType,
    OAuth2Scope,
    CommandType,
    CommandOptionType,
    InteractionType,
    InteractionResponseType,
    ComponentType,
    ButtonStyle,
    TextInputStyle,
)
from .exceptions import (
    ApplicationError,
    ApplicationNotFoundError,
    ApplicationAccessDeniedError,
    ApplicationLimitError,
    CommandNotFoundError,
    CommandLimitError,
    CommandValidationError,
    CommandOptionLimitError,
    InteractionNotFoundError,
    InteractionExpiredError,
    InteractionAlreadyRespondedError,
    InteractionValidationError,
    ComponentValidationError,
    OAuth2Error,
    InvalidClientError,
    InvalidGrantError,
    InvalidScopeError,
    InvalidRedirectUriError,
    AuthorizationCodeExpiredError,
    TokenExpiredError,
    TokenRevokedError,
    InstallationNotFoundError,
    InstallationExistsError,
    WebhookSignatureError,
    WebhookDeliveryError,
    RateLimitError,
    PermissionDeniedError,
)
from .oauth import (
    VALID_SCOPES,
    SCOPE_DESCRIPTIONS,
    validate_scopes,
    parse_scopes,
    scopes_to_string,
)
from .commands import (
    build_option,
    validate_option,
    validate_options,
    validate_command_name,
    validate_command_description,
    validate_command,
)
from .interactions import (
    build_button,
    build_select_menu,
    build_text_input,
    build_action_row,
    build_modal,
    validate_components,
    create_message_response,
    create_deferred_response,
    create_modal_response,
    create_autocomplete_response,
    create_update_response,
)

__all__ = [
    # Models
    "Application",
    "Command",
    "CommandOption",
    "CommandChoice",
    "Interaction",
    "InteractionData",
    "InteractionResponse",
    "ApplicationInstallation",
    "OAuth2Token",
    "OAuth2AuthorizationCode",
    "Button",
    "SelectMenu",
    "SelectOption",
    "TextInput",
    "ActionRow",
    "Modal",
    "WebhookDelivery",
    # Enums
    "ApplicationType",
    "OAuth2Scope",
    "CommandType",
    "CommandOptionType",
    "InteractionType",
    "InteractionResponseType",
    "ComponentType",
    "ButtonStyle",
    "TextInputStyle",
    # Exceptions
    "ApplicationError",
    "ApplicationNotFoundError",
    "ApplicationAccessDeniedError",
    "ApplicationLimitError",
    "CommandNotFoundError",
    "CommandLimitError",
    "CommandValidationError",
    "CommandOptionLimitError",
    "InteractionNotFoundError",
    "InteractionExpiredError",
    "InteractionAlreadyRespondedError",
    "InteractionValidationError",
    "ComponentValidationError",
    "OAuth2Error",
    "InvalidClientError",
    "InvalidGrantError",
    "InvalidScopeError",
    "InvalidRedirectUriError",
    "AuthorizationCodeExpiredError",
    "TokenExpiredError",
    "TokenRevokedError",
    "InstallationNotFoundError",
    "InstallationExistsError",
    "WebhookSignatureError",
    "WebhookDeliveryError",
    "RateLimitError",
    "PermissionDeniedError",
    # OAuth helpers
    "VALID_SCOPES",
    "SCOPE_DESCRIPTIONS",
    "validate_scopes",
    "parse_scopes",
    "scopes_to_string",
    # Command helpers
    "build_option",
    "validate_option",
    "validate_options",
    "validate_command_name",
    "validate_command_description",
    "validate_command",
    # Component helpers
    "build_button",
    "build_select_menu",
    "build_text_input",
    "build_action_row",
    "build_modal",
    "validate_components",
    # Response helpers
    "create_message_response",
    "create_deferred_response",
    "create_modal_response",
    "create_autocomplete_response",
    "create_update_response",
    # Setup
    "setup",
    # Application operations
    "create_application",
    "get_application",
    "get_user_applications",
    "update_application",
    "delete_application",
    "regenerate_client_secret",
    "create_bot_for_application",
    # OAuth operations
    "generate_oauth_url",
    "exchange_code",
    "refresh_token",
    "revoke_token",
    # Command operations
    "register_command",
    "update_command",
    "delete_command",
    "get_commands",
    # Interaction operations
    "handle_interaction",
    "create_interaction_response",
    # Webhook operations
    "verify_webhook_signature",
    # Installation operations
    "install_application",
    "uninstall_application",
    "get_installations",
    # Rate limiting
    "check_rate_limit",
]

_manager = None
_setup_complete = False


def setup(db, auth_module=None, servers_module=None, events_module=None):
    """
    Initialize the applications module.

    Args:
        db: Database instance (must be connected)
        auth_module: Optional auth module for bot account integration
        servers_module: Optional servers module for installation tracking
        events_module: Optional events module for interaction dispatch
    """
    global _manager, _setup_complete

    from .manager import ApplicationManager

    _manager = ApplicationManager(db, auth_module, servers_module, events_module)
    _setup_complete = True


def _get_manager():
    """Get the manager instance, raising if not setup."""
    if not _setup_complete or _manager is None:
        raise RuntimeError(
            "Applications module not initialized. Call applications.setup(db) first."
        )
    return _manager


# === Application Operations ===


def create_application(
    owner_id: int,
    name: str,
    description: Optional[str] = None,
    redirect_uris: Optional[List[str]] = None,
    bot_public: bool = True,
    bot_require_code_grant: bool = False,
    terms_of_service_url: Optional[str] = None,
    privacy_policy_url: Optional[str] = None,
    interactions_endpoint_url: Optional[str] = None,
) -> Application:
    """Create a new application."""
    return _get_manager().create_application(
        owner_id=owner_id,
        name=name,
        description=description,
        redirect_uris=redirect_uris,
        bot_public=bot_public,
        bot_require_code_grant=bot_require_code_grant,
        terms_of_service_url=terms_of_service_url,
        privacy_policy_url=privacy_policy_url,
        interactions_endpoint_url=interactions_endpoint_url,
    )


def get_application(
    application_id: int, user_id: Optional[int] = None
) -> Optional[Application]:
    """Get an application by ID."""
    return _get_manager().get_application(application_id, user_id)


def get_user_applications(user_id: int) -> List[Application]:
    """Get all applications owned by a user."""
    return _get_manager().get_user_applications(user_id)


def update_application(
    user_id: int,
    application_id: int,
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
    """Update an application."""
    return _get_manager().update_application(
        user_id=user_id,
        application_id=application_id,
        name=name,
        description=description,
        icon_url=icon_url,
        redirect_uris=redirect_uris,
        bot_public=bot_public,
        bot_require_code_grant=bot_require_code_grant,
        terms_of_service_url=terms_of_service_url,
        privacy_policy_url=privacy_policy_url,
        interactions_endpoint_url=interactions_endpoint_url,
    )


def delete_application(user_id: int, application_id: int) -> bool:
    """Delete an application."""
    return _get_manager().delete_application(user_id, application_id)


def regenerate_client_secret(user_id: int, application_id: int) -> str:
    """Regenerate the client secret for an application."""
    return _get_manager().regenerate_client_secret(user_id, application_id)


def create_bot_for_application(
    user_id: int,
    application_id: int,
    permissions: Optional[Dict[str, bool]] = None,
) -> Dict[str, Any]:
    """Create a bot account for an application."""
    return _get_manager().create_bot_for_application(
        user_id, application_id, permissions
    )


# === OAuth Operations ===


def generate_oauth_url(
    application_id: int,
    redirect_uri: str,
    scopes: List[str],
    state: Optional[str] = None,
    permissions: Optional[str] = None,
) -> str:
    """Generate an OAuth2 authorization URL."""
    return _get_manager().generate_oauth_url(
        application_id, redirect_uri, scopes, state, permissions
    )


def exchange_code(
    application_id: int,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> Dict[str, Any]:
    """Exchange an authorization code for tokens."""
    return _get_manager().exchange_code(
        application_id, client_secret, code, redirect_uri
    )


def refresh_token(
    application_id: int,
    client_secret: str,
    refresh_token_str: str,
) -> Dict[str, Any]:
    """Refresh an access token."""
    return _get_manager().refresh_token(
        application_id, client_secret, refresh_token_str
    )


def revoke_token(token: str) -> bool:
    """Revoke an OAuth2 token."""
    return _get_manager().revoke_token(token)


# === Command Operations ===


def register_command(
    application_id: int,
    name: str,
    description: str,
    command_type: CommandType = CommandType.CHAT_INPUT,
    server_id: Optional[int] = None,
    options: Optional[List[Dict[str, Any]]] = None,
    default_member_permissions: Optional[str] = None,
    dm_permission: bool = True,
    nsfw: bool = False,
) -> Command:
    """Register a new command."""
    return _get_manager().register_command(
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
    command_id: int,
    name: Optional[str] = None,
    description: Optional[str] = None,
    options: Optional[List[Dict[str, Any]]] = None,
    default_member_permissions: Optional[str] = None,
    dm_permission: Optional[bool] = None,
    nsfw: Optional[bool] = None,
) -> Command:
    """Update a command."""
    return _get_manager().update_command(
        command_id=command_id,
        name=name,
        description=description,
        options=options,
        default_member_permissions=default_member_permissions,
        dm_permission=dm_permission,
        nsfw=nsfw,
    )


def delete_command(command_id: int) -> bool:
    """Delete a command."""
    return _get_manager().delete_command(command_id)


def get_commands(
    application_id: int,
    server_id: Optional[int] = None,
    include_global: bool = True,
) -> List[Command]:
    """Get commands for an application."""
    return _get_manager().get_commands(application_id, server_id, include_global)


# === Interaction Operations ===


def handle_interaction(
    application_id: int,
    interaction_type: InteractionType,
    user_id: int,
    data: Optional[Dict[str, Any]] = None,
    server_id: Optional[int] = None,
    channel_id: Optional[int] = None,
    message_id: Optional[int] = None,
    locale: Optional[str] = None,
    server_locale: Optional[str] = None,
) -> Interaction:
    """Create and handle an interaction."""
    return _get_manager().handle_interaction(
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


def create_interaction_response(
    interaction_token: str,
    response: InteractionResponse,
) -> bool:
    """Respond to an interaction."""
    return _get_manager().create_interaction_response(interaction_token, response)


# === Webhook Operations ===


def verify_webhook_signature(
    body: bytes,
    signature: str,
    timestamp: str,
) -> bool:
    """Verify a webhook request signature."""
    return _get_manager().verify_webhook_signature(body, signature, timestamp)


# === Installation Operations ===


def install_application(
    application_id: int,
    server_id: int,
    installer_id: int,
    permissions: str = "0",
    scopes: Optional[List[str]] = None,
) -> ApplicationInstallation:
    """Install an application on a server."""
    return _get_manager().install_application(
        application_id, server_id, installer_id, permissions, scopes
    )


def uninstall_application(
    application_id: int,
    server_id: int,
    user_id: int,
) -> bool:
    """Uninstall an application from a server."""
    return _get_manager().uninstall_application(application_id, server_id, user_id)


def get_installations(
    application_id: Optional[int] = None,
    server_id: Optional[int] = None,
) -> List[ApplicationInstallation]:
    """Get application installations."""
    return _get_manager().get_installations(application_id, server_id)


# === Rate Limiting ===


def check_rate_limit(application_id: int) -> bool:
    """Check if an application is rate limited."""
    return _get_manager().check_rate_limit(application_id)
