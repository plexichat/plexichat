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


class BulkSettingsRequest(RootModel[Dict[str, Any]]):
    """Request body for bulk setting updates."""

    pass
