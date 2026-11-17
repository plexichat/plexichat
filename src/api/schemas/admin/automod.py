"""
Automod schemas.
"""

from typing import List, Literal, Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict
from ..policy_types import UIText
from ..common import SnowflakeID

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
    exempt_roles: Optional[List[int]] = None
    exempt_channels: Optional[List[int]] = None
    priority: Optional[int] = 0
    check_all: Optional[bool] = False
    enabled: Optional[bool] = True


class AutomodRuleUpdateRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: Optional[UIText] = Field(None, min_length=1, max_length=100)
    config: Optional[Dict[str, Any]] = None
    actions: Optional[List[AutomodRuleAction]] = None
    exempt_roles: Optional[List[int]] = None
    exempt_channels: Optional[List[int]] = None
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
