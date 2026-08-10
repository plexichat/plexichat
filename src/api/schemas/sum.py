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


class SumViewerPermissionsResponse(BaseModel):
    """Effective access for the requesting user (never trusts client labels).

    ``read_only_ranges`` is populated on per-sheet responses (viewport); at
    the workbook level it is empty because range protection is sheet-scoped.
    """

    can_view: bool = True
    can_edit: bool = True
    reason: Optional[str] = None
    read_only_ranges: List["SumRangePermissionResponse"] = Field(default_factory=list)


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
    my_permissions: SumViewerPermissionsResponse = Field(
        default_factory=SumViewerPermissionsResponse
    )


class SumWorkbookListResponse(BaseModel):
    items: List[SumWorkbookResponse] = Field(default_factory=list)
    total: int = 0
    has_more: bool = False


class SumViewportFilter(BaseModel):
    column: int = Field(..., ge=0, le=16384)
    operator: str = Field(
        "eq",
        pattern="^(eq|neq|gt|gte|lt|lte|contains|not_contains|is_empty|is_not_empty)$",
    )
    value: Optional[Any] = None


class SumFreezeInfo(BaseModel):
    freeze_rows: int = 0
    freeze_columns: int = 0


class SumValidationInfo(BaseModel):
    id: SnowflakeID
    row_start: int
    row_end: int
    column_start: int
    column_end: int
    rule_type: str
    criteria: Dict[str, Any] = Field(default_factory=dict)
    allow_blank: bool = True
    message: Optional[str] = None


class SumConditionalFormatInfo(BaseModel):
    id: SnowflakeID
    row_start: int
    row_end: int
    column_start: int
    column_end: int
    rule_type: str
    criteria: Dict[str, Any] = Field(default_factory=dict)
    style: Dict[str, Any] = Field(default_factory=dict)


class SumCommentInfo(BaseModel):
    id: SnowflakeID
    row: int
    column: int
    author_id: SnowflakeID
    body: str
    resolved: bool = False
    created_at: int


class SumViewportResponse(BaseModel):
    workbook_id: SnowflakeID
    sheet_id: SnowflakeID
    revision: int
    row_start: int
    row_end: int
    column_start: int
    column_end: int
    row_count: int = 0
    column_count: int = 0
    has_more: bool = False
    cells: List[Dict[str, Any]] = Field(default_factory=list)
    hidden_rows: List[int] = Field(default_factory=list)
    filters: List[SumViewportFilter] = Field(default_factory=list)
    freeze: SumFreezeInfo = Field(default_factory=SumFreezeInfo)
    validations: List[SumValidationInfo] = Field(default_factory=list)
    conditional_formats: List[SumConditionalFormatInfo] = Field(default_factory=list)
    comments: List[SumCommentInfo] = Field(default_factory=list)
    can_edit: bool = True
    read_only_ranges: List["SumRangePermissionResponse"] = Field(default_factory=list)


class SumCellOperationRequest(BaseModel):
    operation_id: str = Field(..., min_length=1, max_length=200)
    operation_type: str = Field(
        "set_cell",
        pattern="^(set_cell|clear_cell|set_format|insert_rows|delete_rows|insert_columns|delete_columns)$",
    )
    base_revision: int = Field(..., ge=0)
    cells: List[Dict[str, Any]] = Field(..., min_length=1, max_length=2000)


class SumSortRequest(BaseModel):
    column: int = Field(..., ge=0, le=16384)
    start_row: int = Field(0, ge=0)
    end_row: int = Field(..., ge=1)
    direction: str = Field("asc", pattern="^(asc|desc)$")
    base_revision: int = Field(..., ge=0)


class SumSortResponse(BaseModel):
    operation_id: str
    revision: int
    rows: int = 0


class SumOperationResponse(BaseModel):
    operation_id: str
    revision: int
    replayed: bool = False


class SumReplayOperation(BaseModel):
    operation_id: str
    actor_id: SnowflakeID
    base_revision: int
    result_revision: int
    operation_type: str
    sheet_id: Optional[SnowflakeID] = None
    cells: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: int


class SumReplayResponse(BaseModel):
    workbook_id: SnowflakeID
    after_revision: int
    operations: List[SumReplayOperation] = Field(default_factory=list)


class SumSnapshotCreateRequest(BaseModel):
    pass


class SumSnapshotResponse(BaseModel):
    id: SnowflakeID
    workbook_id: SnowflakeID
    revision: int
    checksum: Optional[str] = None
    cell_count: int = 0
    created_at: int


class SumSnapshotListResponse(BaseModel):
    items: List[SumSnapshotResponse] = Field(default_factory=list)


class SumSheetCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    position: Optional[int] = Field(None, ge=0)


class SumSheetRenameRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class SumSheetOpResponse(BaseModel):
    id: SnowflakeID
    name: str
    position: Optional[int] = None
    created_at: Optional[int] = None
    updated_at: Optional[int] = None


class SumCsvExportResponse(BaseModel):
    workbook_id: SnowflakeID
    sheet_id: SnowflakeID
    filename: str
    content: str


class SumCsvImportRequest(BaseModel):
    content: str = Field(..., max_length=2_000_000)
    base_revision: int = Field(..., ge=0)


class SumCsvImportResponse(BaseModel):
    operation_id: str
    revision: int
    rows: int = 0
    cells: int = 0


class SumGrantRequest(BaseModel):
    subject_id: SnowflakeID
    permissions: List[str] = Field(..., min_length=1, max_length=8)
    expires_at: Optional[int] = None


class SumGrantResponse(BaseModel):
    id: SnowflakeID
    workbook_id: SnowflakeID
    subject_id: SnowflakeID
    permissions: List[str] = Field(default_factory=list)
    expires_at: Optional[int] = None
    revoked_at: Optional[int] = None
    created_at: int


class SumGrantListResponse(BaseModel):
    items: List[SumGrantResponse] = Field(default_factory=list)


class SumRangePermissionRequest(BaseModel):
    row_start: int = Field(..., ge=0)
    row_end: int = Field(..., ge=1)
    column_start: int = Field(..., ge=0)
    column_end: int = Field(..., ge=1)
    permission: str = Field(..., pattern="^(read_only|editable)$")


class SumRangePermissionResponse(BaseModel):
    id: SnowflakeID
    workbook_id: SnowflakeID
    sheet_id: SnowflakeID
    row_start: int
    row_end: int
    column_start: int
    column_end: int
    permission: str
    created_at: int


class SumRangePermissionListResponse(BaseModel):
    items: List[SumRangePermissionResponse] = Field(default_factory=list)


class SumWorkbookFileResponse(BaseModel):
    workbook_id: SnowflakeID
    filename: str
    content: str = ""
    size: int = 0


class SumWorkbookUploadRequest(BaseModel):
    content: str = Field(..., description="Base64-encoded .plexisum file bytes")
    base_revision: Optional[int] = Field(None, ge=0)


class SumWorkbookUploadResponse(BaseModel):
    workbook_id: SnowflakeID
    revision: int
    size: int = 0
    checksum: str = ""


class SumFreezeRequest(BaseModel):
    freeze_rows: int = Field(0, ge=0, le=500)
    freeze_columns: int = Field(0, ge=0, le=100)


class SumFreezeResponse(BaseModel):
    sheet_id: SnowflakeID
    freeze_rows: int = 0
    freeze_columns: int = 0


class SumValidationRequest(BaseModel):
    rule_type: str = Field(
        ...,
        pattern="^(whole|decimal|list|length|date)$",
    )
    row_start: int = Field(..., ge=0)
    row_end: int = Field(..., ge=1)
    column_start: int = Field(..., ge=0)
    column_end: int = Field(..., ge=1)
    criteria: Dict[str, Any] = Field(default_factory=dict)
    allow_blank: bool = True
    message: Optional[str] = Field(None, max_length=300)


class SumValidationResponse(BaseModel):
    id: SnowflakeID
    sheet_id: SnowflakeID
    row_start: int
    row_end: int
    column_start: int
    column_end: int
    rule_type: str
    criteria: Dict[str, Any] = Field(default_factory=dict)
    allow_blank: bool = True
    message: Optional[str] = None


class SumValidationListResponse(BaseModel):
    items: List[SumValidationResponse] = Field(default_factory=list)


class SumConditionalFormatRequest(BaseModel):
    rule_type: str = Field(
        ..., pattern="^(cell_value|text_contains|formula|blank|duplicate)$"
    )
    row_start: int = Field(..., ge=0)
    row_end: int = Field(..., ge=1)
    column_start: int = Field(..., ge=0)
    column_end: int = Field(..., ge=1)
    criteria: Dict[str, Any] = Field(default_factory=dict)
    style: Dict[str, Any] = Field(default_factory=dict)


class SumConditionalFormatResponse(BaseModel):
    id: SnowflakeID
    sheet_id: SnowflakeID
    row_start: int
    row_end: int
    column_start: int
    column_end: int
    rule_type: str
    criteria: Dict[str, Any] = Field(default_factory=dict)
    style: Dict[str, Any] = Field(default_factory=dict)


class SumConditionalFormatListResponse(BaseModel):
    items: List[SumConditionalFormatResponse] = Field(default_factory=list)


class SumCommentRequest(BaseModel):
    row: int = Field(..., ge=0)
    column: int = Field(..., ge=0)
    body: str = Field(..., min_length=1, max_length=5000)


class SumCommentResponse(BaseModel):
    id: SnowflakeID
    sheet_id: SnowflakeID
    row: int
    column: int
    author_id: SnowflakeID
    body: str
    resolved: bool = False
    created_at: int
    updated_at: int = 0


class SumCommentListResponse(BaseModel):
    items: List[SumCommentResponse] = Field(default_factory=list)


class SumPresenceResponse(BaseModel):
    workbook_id: SnowflakeID
    users: List[Dict[str, Any]] = Field(default_factory=list)


class SumXlsxExportResponse(BaseModel):
    workbook_id: SnowflakeID
    filename: str
    content: str = ""
    size: int = 0


class SumXlsxImportRequest(BaseModel):
    content: str = Field(..., description="Base64-encoded XLSX file bytes")
    base_revision: int = Field(..., ge=0)


# Late-bound forward references: ``SumViewportResponse`` and
# ``SumViewerPermissionsResponse`` reference ``SumRangePermissionResponse``,
# which is defined later in this module. Rebuild explicitly so the schemas are
# fully resolvable on import regardless of pydantic's lazy resolution.
SumViewportResponse.model_rebuild()
SumViewerPermissionsResponse.model_rebuild()
