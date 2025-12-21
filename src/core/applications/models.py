"""
Application models - Dataclasses for all application-related entities.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum, IntEnum


class ApplicationType(Enum):
    """Type of application."""
    BOT = "bot"
    WEBHOOK = "webhook"
    OAUTH = "oauth"


class OAuth2Scope(Enum):
    """OAuth2 scopes for application authorization."""
    IDENTIFY = "identify"
    EMAIL = "email"
    GUILDS = "guilds"
    GUILDS_JOIN = "guilds.join"
    GUILDS_MEMBERS_READ = "guilds.members.read"
    BOT = "bot"
    APPLICATIONS_COMMANDS = "applications.commands"
    APPLICATIONS_COMMANDS_UPDATE = "applications.commands.update"
    MESSAGES_READ = "messages.read"
    WEBHOOK_INCOMING = "webhook.incoming"


class CommandType(IntEnum):
    """Type of application command."""
    CHAT_INPUT = 1
    USER = 2
    MESSAGE = 3


class CommandOptionType(IntEnum):
    """Type of command option."""
    SUB_COMMAND = 1
    SUB_COMMAND_GROUP = 2
    STRING = 3
    INTEGER = 4
    BOOLEAN = 5
    USER = 6
    CHANNEL = 7
    ROLE = 8
    MENTIONABLE = 9
    NUMBER = 10
    ATTACHMENT = 11


class InteractionType(IntEnum):
    """Type of interaction."""
    PING = 1
    APPLICATION_COMMAND = 2
    MESSAGE_COMPONENT = 3
    APPLICATION_COMMAND_AUTOCOMPLETE = 4
    MODAL_SUBMIT = 5


class InteractionResponseType(IntEnum):
    """Type of interaction response."""
    PONG = 1
    CHANNEL_MESSAGE_WITH_SOURCE = 4
    DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE = 5
    DEFERRED_UPDATE_MESSAGE = 6
    UPDATE_MESSAGE = 7
    APPLICATION_COMMAND_AUTOCOMPLETE_RESULT = 8
    MODAL = 9


class ComponentType(IntEnum):
    """Type of message component."""
    ACTION_ROW = 1
    BUTTON = 2
    STRING_SELECT = 3
    TEXT_INPUT = 4
    USER_SELECT = 5
    ROLE_SELECT = 6
    MENTIONABLE_SELECT = 7
    CHANNEL_SELECT = 8


class ButtonStyle(IntEnum):
    """Style of button component."""
    PRIMARY = 1
    SECONDARY = 2
    SUCCESS = 3
    DANGER = 4
    LINK = 5


class TextInputStyle(IntEnum):
    """Style of text input component."""
    SHORT = 1
    PARAGRAPH = 2


@dataclass
class Application:
    """Represents a registered application."""
    id: int
    owner_id: int
    name: str
    description: Optional[str]
    icon_url: Optional[str]
    bot_id: Optional[int]
    bot_public: bool
    bot_require_code_grant: bool
    terms_of_service_url: Optional[str]
    privacy_policy_url: Optional[str]
    redirect_uris: List[str]
    interactions_endpoint_url: Optional[str]
    created_at: int
    updated_at: int

    client_secret: Optional[str] = field(default=None, repr=False)
    client_secret_hash: Optional[str] = field(default=None, repr=False)


@dataclass
class CommandOption:
    """Represents a command option."""
    name: str
    description: str
    option_type: CommandOptionType
    required: bool = False
    choices: Optional[List[Dict[str, Any]]] = None
    options: Optional[List["CommandOption"]] = None
    channel_types: Optional[List[int]] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    autocomplete: bool = False


@dataclass
class Command:
    """Represents an application command."""
    id: int
    application_id: int
    name: str
    description: str
    command_type: CommandType
    server_id: Optional[int]
    options: List[CommandOption]
    default_member_permissions: Optional[str]
    dm_permission: bool
    nsfw: bool
    version: int
    created_at: int
    updated_at: int


@dataclass
class CommandChoice:
    """Represents a choice for a command option."""
    name: str
    value: Any


@dataclass
class InteractionData:
    """Data payload for an interaction."""
    id: int
    name: str
    command_type: Optional[CommandType] = None
    resolved: Optional[Dict[str, Any]] = None
    options: Optional[List[Dict[str, Any]]] = None
    custom_id: Optional[str] = None
    component_type: Optional[ComponentType] = None
    values: Optional[List[str]] = None
    target_id: Optional[int] = None
    components: Optional[List[Dict[str, Any]]] = None


@dataclass
class Interaction:
    """Represents an interaction from a user."""
    id: int
    application_id: int
    interaction_type: InteractionType
    data: Optional[InteractionData]
    server_id: Optional[int]
    channel_id: Optional[int]
    user_id: int
    token: str
    version: int
    message_id: Optional[int]
    locale: Optional[str]
    server_locale: Optional[str]
    created_at: int
    responded: bool = False

    token_hash: Optional[str] = field(default=None, repr=False)


@dataclass
class InteractionResponse:
    """Response to an interaction."""
    response_type: InteractionResponseType
    content: Optional[str] = None
    embeds: Optional[List[Dict[str, Any]]] = None
    components: Optional[List[Dict[str, Any]]] = None
    flags: int = 0
    tts: bool = False
    allowed_mentions: Optional[Dict[str, Any]] = None
    attachments: Optional[List[Dict[str, Any]]] = None
    choices: Optional[List[Dict[str, Any]]] = None
    custom_id: Optional[str] = None
    title: Optional[str] = None


@dataclass
class Button:
    """Represents a button component."""
    style: ButtonStyle
    label: Optional[str] = None
    emoji: Optional[Dict[str, Any]] = None
    custom_id: Optional[str] = None
    url: Optional[str] = None
    disabled: bool = False


@dataclass
class SelectOption:
    """Represents a select menu option."""
    label: str
    value: str
    description: Optional[str] = None
    emoji: Optional[Dict[str, Any]] = None
    default: bool = False


@dataclass
class SelectMenu:
    """Represents a select menu component."""
    custom_id: str
    component_type: ComponentType
    options: Optional[List[SelectOption]] = None
    channel_types: Optional[List[int]] = None
    placeholder: Optional[str] = None
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False


@dataclass
class TextInput:
    """Represents a text input component."""
    custom_id: str
    style: TextInputStyle
    label: str
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    required: bool = True
    value: Optional[str] = None
    placeholder: Optional[str] = None


@dataclass
class ActionRow:
    """Represents an action row container."""
    components: List[Any]


@dataclass
class Modal:
    """Represents a modal dialog."""
    custom_id: str
    title: str
    components: List[ActionRow]


@dataclass
class OAuth2Token:
    """Represents an OAuth2 access token."""
    id: int
    application_id: int
    user_id: int
    access_token_hash: str
    refresh_token_hash: Optional[str]
    scopes: List[str]
    expires_at: int
    created_at: int
    revoked: bool = False

    access_token: Optional[str] = field(default=None, repr=False)
    refresh_token: Optional[str] = field(default=None, repr=False)


@dataclass
class OAuth2AuthorizationCode:
    """Represents an OAuth2 authorization code."""
    id: int
    application_id: int
    user_id: int
    code_hash: str
    redirect_uri: str
    scopes: List[str]
    expires_at: int
    created_at: int
    used: bool = False

    code: Optional[str] = field(default=None, repr=False)


@dataclass
class ApplicationInstallation:
    """Represents an application installation on a server."""
    id: int
    application_id: int
    server_id: int
    installer_id: int
    permissions: str
    scopes: List[str]
    created_at: int
    updated_at: int


@dataclass
class WebhookDelivery:
    """Represents a webhook delivery attempt."""
    id: int
    application_id: int
    interaction_id: int
    endpoint_url: str
    request_body: str
    response_status: Optional[int]
    response_body: Optional[str]
    delivered_at: int
    success: bool
