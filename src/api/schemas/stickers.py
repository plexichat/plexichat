"""
Sticker schemas - Pydantic models for sticker endpoints.
"""

from typing import Optional, List
from typing_extensions import Annotated
from pydantic import BaseModel, Field, ConfigDict, BeforeValidator


class StickerResponse(BaseModel):
    """Sticker response model."""

    model_config = ConfigDict(from_attributes=True)

    id: Annotated[str, BeforeValidator(lambda v: str(v) if v is not None else v)] = (
        Field(..., description="Sticker ID")
    )
    pack_id: Annotated[
        str, BeforeValidator(lambda v: str(v) if v is not None else v)
    ] = Field(..., description="Pack ID")
    name: str = Field(..., description="Sticker name")
    format: str = Field(..., description="Sticker format (png, apng, json)")
    tags: List[str] = Field(default_factory=list, description="Sticker tags")
    related_emoji: Optional[str] = Field(None, description="Related unicode emoji")
    url: str = Field(..., description="Sticker URL")
    size: int = Field(..., description="File size in bytes")
    width: Optional[int] = Field(None, description="Image width")
    height: Optional[int] = Field(None, description="Image height")
    available: bool = Field(True, description="Whether sticker is available")


class StickerPackResponse(BaseModel):
    """Sticker pack response model."""

    model_config = ConfigDict(from_attributes=True)

    id: Annotated[str, BeforeValidator(lambda v: str(v) if v is not None else v)] = (
        Field(..., description="Pack ID")
    )
    name: str = Field(..., description="Pack name")
    description: Optional[str] = Field(None, description="Pack description")
    pack_type: str = Field(..., description="Pack type")
    server_id: Optional[
        Annotated[str, BeforeValidator(lambda v: str(v) if v is not None else v)]
    ] = Field(None, description="Server ID")
    sticker_count: int = Field(0, description="Number of stickers in pack")
    stickers: List[StickerResponse] = Field(
        default_factory=list, description="Stickers in this pack"
    )
