"""
Automod schemas.
"""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict


class AutomodRuleAction(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    action_type: str
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

    server_id: int
    name: str = Field(..., min_length=1, max_length=100)
    rule_type: str
    config: Dict[str, Any]
    actions: List[AutomodRuleAction]
    exempt_roles: Optional[List[int]] = None
    exempt_channels: Optional[List[int]] = None
    priority: Optional[int] = 0
    check_all: Optional[bool] = False
    enabled: Optional[bool] = True


class AutomodRuleUpdateRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: Optional[str] = Field(None, min_length=1, max_length=100)
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
