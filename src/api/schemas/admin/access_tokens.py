"""
Access token schemas.
"""

from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict


class AccessTokenCreateRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    token: Optional[str] = Field(None, min_length=32, max_length=128)
    expires_at: Optional[int] = Field(
        None, description="Unix timestamp when token expires"
    )
    scope_mode: str = Field(
        "none",
        pattern="^(none|monitor|enforce)$",
        description="IP scope enforcement mode",
    )


class AccessTokenResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: Optional[str]
    description: Optional[str]
    created_by: Optional[str]
    created_at: int
    first_used_at: Optional[int]
    last_used_at: Optional[int]
    last_used_ip_address: Optional[str]
    last_used_user_agent: Optional[str]
    last_used_path: Optional[str]
    expires_at: Optional[int]
    scope_mode: str
    use_count_total: int
    distinct_ip_count: int
    denied_count_total: int
    revoked: bool
    revoked_at: Optional[int]
    revoked_by: Optional[str]


class AccessTokenCreateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    token: str
    access_token: AccessTokenResponse


class AccessTokenUpdateRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    expires_at: Optional[int] = Field(None)
    clear_expiry: bool = Field(False)
    scope_mode: Optional[str] = Field(
        None,
        pattern="^(none|monitor|enforce)$",
        description="IP scope enforcement mode",
    )


class AccessTokenRotateRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    token: Optional[str] = Field(None, min_length=32, max_length=128)


class AccessTokenScopeCreateRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    scope_type: str = Field(..., pattern="^(ip|cidr)$")
    value: str = Field(..., min_length=1, max_length=128)


class AccessTokenScopeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scope_type: str
    value: str
    created_by: Optional[str]
    created_at: int


class AccessTokenUsageIPResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ip_address: str
    request_count: int
    denied_count: int
    last_seen_at: Optional[int]


class AccessTokenUsagePathResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    method: Optional[str]
    path: Optional[str]
    request_count: int
    last_seen_at: Optional[int]


class AccessTokenUsageEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    used_at: int
    ip_address: Optional[str]
    method: Optional[str]
    path: Optional[str]
    user_agent: Optional[str]
    allowed: bool
    scope_match: Optional[bool]
    reject_reason: Optional[str]


class AccessTokenDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    access_token: AccessTokenResponse
    scopes: List[AccessTokenScopeResponse]
    recent_events: List[AccessTokenUsageEventResponse]
    top_ips: List[AccessTokenUsageIPResponse]
    top_paths: List[AccessTokenUsagePathResponse]
    total_events: int
    distinct_ip_count: int
    denied_count_total: int
