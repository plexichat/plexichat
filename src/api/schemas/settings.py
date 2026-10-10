from pydantic import BaseModel, Field, ConfigDict, RootModel
from typing import Dict, Any


class SettingValue(BaseModel):
    """Request body for setting a value."""

    value: str = Field(..., max_length=10000, description="Setting value")


class SettingResponse(BaseModel):
    """Response for a single setting."""

    model_config = ConfigDict(from_attributes=True)

    key: str = Field(..., description="Setting key")
    value: str = Field(..., description="Setting value")
    created_at: int = Field(..., description="Creation timestamp (Unix)")
    updated_at: int = Field(..., description="Last update timestamp (Unix)")


class SettingsResponse(BaseModel):
    """Response for all settings."""

    model_config = ConfigDict(from_attributes=True)

    settings: Dict[str, str] = Field(..., description="Key-value pairs of settings")
    count: int = Field(..., description="Number of settings")
    limit: int = Field(..., description="Max number of settings allowed")


class BulkSettingsResponse(BaseModel):
    """Response for a bulk settings update."""

    success: bool = Field(..., description="Whether all updates succeeded")
    updated: int = Field(..., description="Number of settings updated")
    failed: int = Field(..., description="Number of settings that failed to update")
    failures: Dict[str, str] = Field(
        default_factory=dict, description="Per-key failure reasons"
    )


class BulkSettingsRequest(RootModel[Dict[str, Any]]):
    """Request body for bulk setting updates."""

    pass
