"""Pydantic schemas for PlexiSum workbook APIs."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .common import SnowflakeID


class SumWorkbookCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    scope: str = Field("personal", pattern="^(personal|server)$")
    server_id: Optional[SnowflakeID] = None


class SumSheetResponse(BaseModel):
    id: SnowflakeID
    name: str
    position: int
    created_at: int
    updated_at: int


class SumWorkbookResponse(BaseModel):
    id: SnowflakeID
    artifact_id: Optional[SnowflakeID]
    owner_id: SnowflakeID
    title: str
    scope: str
    server_id: Optional[SnowflakeID]
    current_revision: int
    sheets: List[SumSheetResponse] = Field(default_factory=list)
    created_at: int
    updated_at: int


class SumWorkbookListResponse(BaseModel):
    items: List[SumWorkbookResponse] = Field(default_factory=list)
    total: int = 0
    has_more: bool = False


class SumViewportResponse(BaseModel):
    workbook_id: SnowflakeID
    sheet_id: SnowflakeID
    revision: int
    row_start: int
    row_end: int
    column_start: int
    column_end: int
    cells: List[Dict[str, Any]] = Field(default_factory=list)


class SumCellOperationRequest(BaseModel):
    operation_id: str = Field(..., min_length=1, max_length=200)
    operation_type: str = Field("set_cell", pattern="^set_cell$")
    base_revision: int = Field(..., ge=0)
    cells: List[Dict[str, Any]] = Field(..., min_length=1, max_length=2000)


class SumOperationResponse(BaseModel):
    operation_id: str
    revision: int
    replayed: bool = False
