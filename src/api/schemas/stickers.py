"""
Sticker schemas - Pydantic models for sticker endpoints.
"""

from typing import Optional, List, Any
from pydantic import BaseModel, Field, ConfigDict, field_serializer
from src.core.stickers.models import StickerFormat, PackType

class StickerResponse(BaseModel):
    """Sticker response model."""
    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Sticker ID")
    pack_id: str = Field(..., description="Pack ID")
    name: str = Field(..., description="Sticker name")
    format: str = Field(..., description="Sticker format (png, apng, json)")
    tags: List[str] = Field(default_factory=list, description="Sticker tags")
    related_emoji: Optional[str] = Field(None, description="Related unicode emoji")
    url: str = Field(..., description="Sticker image URL")
    width: Optional[int] = Field(None, description="Image width")
    height: Optional[int] = Field(None, description="Image height")
    available: bool = Field(True, description="Whether sticker is available")
    
    @field_serializer("id", "pack_id")
    def serialize_ids(self, v: Any) -> Optional[str]:
        return str(v) if v else None
        
    @field_serializer("format")
    def serialize_format(self, v: Any) -> str:
        if hasattr(v, "value"):
            return v.value
        return str(v)

class StickerPackResponse(BaseModel):
    """Sticker pack response model."""
    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Pack ID")
    name: str = Field(..., description="Pack name")
    description: Optional[str] = Field(None, description="Pack description")
    pack_type: str = Field(..., description="Pack type")
    server_id: Optional[str] = Field(None, description="Server ID")
    sticker_count: int = Field(0, description="Number of stickers in pack")
    stickers: List[StickerResponse] = Field(default_factory=list, description="Stickers in this pack")
    
    @field_serializer("id", "server_id")
    def serialize_ids(self, v: Any) -> Optional[str]:
        return str(v) if v else None

    @field_serializer("pack_type")
    def serialize_pack_type(self, v: Any) -> str:
        if hasattr(v, "value"):
            return v.value
        return str(v)
