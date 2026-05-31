"""
Telemetry schemas.
"""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict


class TelemetryEndpointStat(BaseModel):
    """Statistics for a single endpoint."""

    model_config = ConfigDict(from_attributes=True)

    endpoint: str = Field(..., description="Endpoint path")
    method: str = Field(..., description="HTTP method")
    count: int = Field(..., description="Request count")
    avg_ms: float = Field(..., description="Average response time in ms")
    min_ms: Optional[float] = Field(None, description="Minimum response time in ms")
    max_ms: Optional[float] = Field(None, description="Maximum response time in ms")
    p50_ms: Optional[float] = Field(
        None, description="50th percentile response time in ms"
    )
    p95_ms: float = Field(..., description="95th percentile response time in ms")
    p99_ms: Optional[float] = Field(
        None, description="99th percentile response time in ms"
    )
    error_rate: float = Field(..., description="Error rate percentage")
    error_count: int = Field(0, description="Total error count")
    avg_queries: Optional[float] = Field(
        0.0, description="Average DB queries per request"
    )
    avg_query_time_ms: Optional[float] = Field(
        0.0, description="Average DB query time in ms"
    )


class SystemMetrics(BaseModel):
    """System health metrics."""

    cpu_percent: float = Field(..., description="CPU usage percentage")
    memory_percent: float = Field(..., description="Memory usage percentage")
    memory_used_mb: float = Field(..., description="Memory used in MB")
    memory_total_mb: float = Field(..., description="Total memory in MB")
    disk_percent: float = Field(..., description="Disk usage percentage")
    process_memory_mb: float = Field(..., description="Process RSS memory in MB")
    thread_count: int = Field(..., description="Number of active threads")
    uptime_seconds: float = Field(..., description="Process uptime in seconds")


class AdminDashboardResponse(BaseModel):
    """Admin dashboard data."""

    model_config = ConfigDict(from_attributes=True)

    tickets: Dict[str, int] = Field(..., description="Ticket counts by status")
    telemetry: List[TelemetryEndpointStat] = Field(
        ..., description="Top telemetry stats"
    )
    active_users: int = Field(0, description="Active users in last 24h")
    total_users: int = Field(0, description="Total registered users")
    scheduled_deletions: int = Field(
        0, description="Users with scheduled account deletion"
    )
    db_status: str = Field("healthy", description="Database connection health")
    system: Optional[SystemMetrics] = Field(None, description="System health metrics")
    server_version: str = Field(..., description="Current server version string")
    feature_stats: Dict[str, Any] = Field(
        default_factory=dict,
        description="Usage statistics for new features (bookmarks, scheduled, voice, etc.)",
    )


class TelemetryStatsResponse(BaseModel):
    """Telemetry statistics response."""

    model_config = ConfigDict(from_attributes=True)

    stats: List[TelemetryEndpointStat] = Field(..., description="Endpoint statistics")
    source: str = Field(..., description="Data source filter applied")


class TelemetryHistoryBucket(BaseModel):
    """A single time bucket in telemetry history."""

    model_config = ConfigDict(from_attributes=True)

    timestamp: int = Field(..., description="Bucket start timestamp")
    avg_ms: float = Field(..., description="Average response time in this bucket")
    count: int = Field(..., description="Request count in this bucket")
    error_count: int = Field(..., description="Error count in this bucket")


class TelemetryHistoryResponse(BaseModel):
    """Telemetry history response."""

    model_config = ConfigDict(from_attributes=True)

    history: List[TelemetryHistoryBucket] = Field(
        ..., description="History data buckets"
    )


class TelemetryResetResponse(BaseModel):
    """Telemetry reset response."""

    model_config = ConfigDict(from_attributes=True)

    success: bool = Field(..., description="Whether reset was successful")
    deleted_count: int = Field(..., description="Number of records deleted")


class TelemetryExportResponse(BaseModel):
    """Telemetry export response (JSON format)."""

    model_config = ConfigDict(from_attributes=True)

    export_time: str = Field(..., description="Export generation time")
    hours: int = Field(..., description="Time range in hours")
    stats: List[TelemetryEndpointStat] = Field(..., description="Exported statistics")
