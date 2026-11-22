"""
Automod schemas.
"""

from typing import List, Literal, Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict, field_validator
from ..policy_types import UIText
from ..common import SnowflakeID, SnowflakeIDParam
from src.core.security.outbound import OutboundUrlRejected, parse_outbound_url

#: Exempt role/channel lists are persisted in the rule row and consulted on
#: every message check (``channel_id in rule.exempt_channels`` and
#: ``user_role_ids & set(rule.exempt_roles)``). Cap the list so one rule cannot
#: turn each check into an unbounded scan/allocation.
MAX_EXEMPT_IDS = 256

#: AutoMod AI backend URLs are persisted into the global config and later used
#: as outbound HTTP targets by the adapters (they POST every moderated message
#: to them, together with the configured API key). The admin config route is
#: the write boundary for those URLs. Without a gate here an account with only
#: ``admin.moderation.config`` (not the instance operator) could point the
#: "custom"/"openai" backends at loopback, cloud metadata or an internal service
#: and use the server as an SSRF proxy. ``shieldstral`` is deliberately excluded:
#: it is documented as a local compose service (``http://shieldstral:8080``) and
#: must keep reaching internal addresses.
MAX_AI_URL_LENGTH = 2048
_AI_BACKEND_URL_FIELDS = {
    "custom": ("endpoint_url", False),
    "openai": ("api_url", True),
}
_AI_BACKEND_NAMES = frozenset(_AI_BACKEND_URL_FIELDS)


def _validate_ai_backend_urls(
    value: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Reject unsafe outbound URLs in an AutoMod AI config update.

    Every URL the "custom" and "openai" adapters fetch goes through the shared
    outbound gate (http(s) scheme, no embedded credentials or backslash
    authority, DNS resolution with private/link-local/metadata rejection).
    Rejections are reported generically so a denied URL is not a free internal
    port scan. This only tightens the write boundary; it relaxes no adapter
    check.
    """
    if value is None:
        return value
    if not isinstance(value, dict):
        raise ValueError("ai must be an object")

    for backend_name, backend_config in value.items():
        if backend_name not in _AI_BACKEND_NAMES or backend_config is None:
            continue
        if not isinstance(backend_config, dict):
            raise ValueError(f"ai.{backend_name} must be an object")

        field_name, require_https = _AI_BACKEND_URL_FIELDS[backend_name]
        url = backend_config.get(field_name)
        if url is None:
            continue
        if not isinstance(url, str) or not url.strip():
            raise ValueError(f"Invalid {backend_name} {field_name}")
        if len(url) > MAX_AI_URL_LENGTH:
            raise ValueError(f"Invalid {backend_name} {field_name}")
        if require_https and not url.strip().lower().startswith("https://"):
            raise ValueError(f"Invalid {backend_name} {field_name}")
        try:
            parse_outbound_url(url, f"ai.{backend_name}.{field_name}")
        except (OutboundUrlRejected, ValueError) as exc:
            raise ValueError(f"Invalid or unsafe {backend_name} {field_name}") from exc

    return value


#: Rule/action kinds the automod engine knows about (``RuleType`` / ``ActionType``).
#: The route already converts through those enums; declaring the values here
#: rejects an unknown kind at validation instead of deep in rule creation.
RuleTypeValue = Literal[
    "keyword",
    "regex",
    "mention_spam",
    "message_spam",
    "invite_links",
    "mass_emoji",
    "caps_percentage",
    "repeated_chars",
    "external_links",
    "ai_moderation",
]
ActionTypeValue = Literal[
    "delete_message",
    "timeout_user",
    "kick_user",
    "ban_user",
    "alert_moderators",
    "log_only",
]


class AutomodRuleAction(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    action_type: ActionTypeValue
    duration_seconds: Optional[int] = None
    reason: Optional[str] = None
    notify_user: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AutomodRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    server_id: str
    name: str
    rule_type: str
    enabled: bool
    config: Dict[str, Any]
    actions: List[AutomodRuleAction]
    exempt_roles: List[str]
    exempt_channels: List[str]
    priority: int
    check_all: bool
    created_at: int
    updated_at: int
    created_by: str


class AutomodRuleCreateRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    server_id: SnowflakeID
    name: UIText = Field(..., min_length=1, max_length=100)
    rule_type: RuleTypeValue
    config: Dict[str, Any]
    actions: List[AutomodRuleAction]
    # Snowflake IDs, not arbitrary ints: SnowflakeIDParam rejects the bool /
    # float / negative / >64-bit values lax ``int`` silently coerced here.
    exempt_roles: Optional[List[SnowflakeIDParam]] = Field(
        None, max_length=MAX_EXEMPT_IDS, description="Exempt role IDs"
    )
    exempt_channels: Optional[List[SnowflakeIDParam]] = Field(
        None, max_length=MAX_EXEMPT_IDS, description="Exempt channel IDs"
    )
    priority: Optional[int] = 0
    check_all: Optional[bool] = False
    enabled: Optional[bool] = True


class AutomodRuleUpdateRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: Optional[UIText] = Field(None, min_length=1, max_length=100)
    config: Optional[Dict[str, Any]] = None
    actions: Optional[List[AutomodRuleAction]] = None
    # Snowflake IDs, not arbitrary ints: SnowflakeIDParam rejects the bool /
    # float / negative / >64-bit values lax ``int`` silently coerced here.
    exempt_roles: Optional[List[SnowflakeIDParam]] = Field(
        None, max_length=MAX_EXEMPT_IDS, description="Exempt role IDs"
    )
    exempt_channels: Optional[List[SnowflakeIDParam]] = Field(
        None, max_length=MAX_EXEMPT_IDS, description="Exempt channel IDs"
    )
    priority: Optional[int] = None
    check_all: Optional[bool] = None
    enabled: Optional[bool] = None


class AutomodConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    enabled: bool
    ai: Dict[str, Any] = Field(default_factory=dict)


class AutomodConfigUpdateRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    enabled: Optional[bool] = None
    ai: Optional[Dict[str, Any]] = None

    @field_validator("ai")
    @classmethod
    def _safe_ai_urls(cls, value):
        return _validate_ai_backend_urls(value)
