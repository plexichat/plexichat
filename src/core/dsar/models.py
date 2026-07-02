from dataclasses import dataclass
from typing import Optional, Dict, Any
from enum import Enum


class DSARStatus(Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    GENERATING = "generating"
    READY = "ready"
    EXPIRED = "expired"
    DOWNLOADED = "downloaded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class DSARRequest:
    id: int
    user_id: int
    status: DSARStatus
    requested_at: int
    completed_at: Optional[int]
    expires_at: Optional[int]
    format: str
    file_path_encrypted: Optional[str]
    checksum: Optional[str]
    file_size_bytes: Optional[int]
    admin_id: Optional[int]
    denial_reason: Optional[str]
    error_message: Optional[str]
    metadata: Optional[Dict[str, Any]]


@dataclass
class ExportManifest:
    id: int
    request_id: int
    table_name: str
    record_count: int
    exported_at: int
