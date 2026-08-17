"""Pydantic schemas for the PlexiStash metadata API."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from .common import SnowflakeID


class StashItemCreateRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    item_type: str = Field(..., pattern="^(file|link|note|shortcut)$")
    title: str = Field(..., min_length=1, max_length=500)
    scope: str = Field("personal", pattern="^(personal|server)$")
    server_id: Optional[SnowflakeID] = None
    description: Optional[str] = Field(None, max_length=4000)
    collection_id: Optional[SnowflakeID] = None
    media_object_id: Optional[str] = Field(None, max_length=500)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list, max_length=100)


class StashItemUpdateRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    title: Optional[str] = Field(None, min_length=1, max_length=500)
    description: Optional[str] = Field(None, max_length=4000)
    collection_id: Optional[SnowflakeID] = None
    media_object_id: Optional[str] = Field(None, max_length=500)
    metadata: Optional[Dict[str, Any]] = None
    tags: Optional[List[str]] = Field(None, max_length=100)


class StashFileUploadRequest(BaseModel):
    """Upload a file's bytes straight into Stash (media object + item)."""

    title: str = Field(..., min_length=1, max_length=500)
    filename: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1, description="Base64-encoded file bytes")
    content_type: Optional[str] = Field(None, max_length=255)
    scope: str = Field("personal", pattern="^(personal|server)$")
    server_id: Optional[SnowflakeID] = None
    description: Optional[str] = Field(None, max_length=4000)
    tags: List[str] = Field(default_factory=list, max_length=100)


class StashItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: SnowflakeID
    owner_id: SnowflakeID
    item_type: str
    title: str
    description: Optional[str]
    scope: str
    server_id: Optional[SnowflakeID]
    collection_id: Optional[SnowflakeID]
    media_object_id: Optional[str]
    current_version_id: Optional[SnowflakeID]
    metadata: Dict[str, Any]
    tags: List[str]
    pinned: bool
    created_at: int
    updated_at: int
    grants: Optional[List[Dict[str, Any]]] = None


class StashItemListResponse(BaseModel):
    items: List[StashItemResponse] = Field(default_factory=list)
    total: int = 0
    has_more: bool = False


class StashPinRequest(BaseModel):
    pinned: bool


class StashVersionCreateRequest(BaseModel):
    media_object_id: Optional[str] = Field(None, max_length=500)
    content_type: Optional[str] = Field(None, max_length=255)
    size_bytes: Optional[int] = Field(None, ge=0)
    checksum: Optional[str] = Field(None, max_length=128)


class StashVersionResponse(BaseModel):
    id: SnowflakeID
    item_id: SnowflakeID
    created_by: SnowflakeID
    media_object_id: Optional[str]
    content_type: Optional[str]
    size_bytes: Optional[int]
    checksum: Optional[str]
    created_at: int


class StashGrantRequest(BaseModel):
    subject_id: SnowflakeID
    permissions: List[str] = Field(..., min_length=1, max_length=8)
    expires_at: Optional[int] = Field(None, ge=0)


class StashGrantResponse(BaseModel):
    id: SnowflakeID
    item_id: SnowflakeID
    subject_type: str
    subject_id: SnowflakeID
    permissions: List[str]
    expires_at: Optional[int]


class StashDownloadTicketResponse(BaseModel):
    ticket: str
    item_id: SnowflakeID
    version_id: Optional[SnowflakeID] = None
    media_object_id: Optional[str] = None
    expires_at: int


class StashDownloadRedeemRequest(BaseModel):
    ticket: str = Field(..., min_length=32, max_length=256)


class StashDownloadRedeemResponse(BaseModel):
    item_id: SnowflakeID
    version_id: Optional[SnowflakeID] = None
    media_object_id: Optional[str] = None
    media_url: Optional[str] = None
    expires_at: int


class StashCollectionCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    server_id: Optional[SnowflakeID] = None
    parent_id: Optional[SnowflakeID] = None


class StashCollectionResponse(BaseModel):
    id: SnowflakeID
    owner_id: SnowflakeID
    name: str
    server_id: Optional[SnowflakeID]
    parent_id: Optional[SnowflakeID] = None
    created_at: int
    updated_at: int


class StashCollectionListResponse(BaseModel):
    items: List[StashCollectionResponse] = Field(default_factory=list)


class StashCollectionGrantRequest(BaseModel):
    subject_id: SnowflakeID
    permissions: List[str] = Field(..., min_length=1, max_length=8)
    expires_at: Optional[int] = Field(None, ge=0)


class StashCollectionGrantResponse(BaseModel):
    id: SnowflakeID
    collection_id: SnowflakeID
    subject_id: SnowflakeID
    permissions: List[str]
    expires_at: Optional[int] = None


class StashCollectionGrantListResponse(BaseModel):
    items: List[StashCollectionGrantResponse] = Field(default_factory=list)


class StashShareLinkCreateRequest(BaseModel):
    password: Optional[str] = Field(None, min_length=1, max_length=128)
    permissions: Optional[List[str]] = Field(None, max_length=4)
    expires_at: Optional[int] = Field(None, ge=0)


class StashShareLinkResponse(BaseModel):
    id: SnowflakeID
    item_id: SnowflakeID
    token: str
    password_protected: bool
    permissions: List[str]
    expires_at: Optional[int] = None
    created_at: int


class StashShareLinkSummary(BaseModel):
    id: SnowflakeID
    item_id: SnowflakeID
    password_protected: bool
    permissions: List[str]
    expires_at: Optional[int] = None
    revoked_at: Optional[int] = None
    access_count: int
    created_at: int


class StashShareLinkListResponse(BaseModel):
    items: List[StashShareLinkSummary] = Field(default_factory=list)


class StashShareLinkResolveRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=256)
    password: Optional[str] = Field(None, max_length=128)


class StashShareLinkResolveResponse(BaseModel):
    item_id: SnowflakeID
    media_object_id: Optional[str] = None
    permissions: List[str]
    expires_at: Optional[int] = None


class StashShareDownloadRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=256)
    password: Optional[str] = Field(None, max_length=128)
    version_id: Optional[SnowflakeID] = None
    ttl_seconds: Optional[int] = Field(None, ge=1, le=300)


class StashVersionDiffResponse(BaseModel):
    changed: bool
    binary: bool
    diff: List[Dict[str, Any]] = Field(default_factory=list)
    a_size: int = 0
    b_size: int = 0
