"""
Device-trust & session-duration overview schemas.
"""

from typing import List, Optional
from pydantic import BaseModel, Field


class SessionDurationPolicy(BaseModel):
    """Effective session-duration policy with defaults applied."""

    expire_hours: int = Field(
        ..., description="Session TTL for untrusted devices (hours)"
    )
    trusted_expire_hours: Optional[int] = Field(
        None, description="Session TTL for trusted devices; null = override disabled"
    )
    trusted_override_enabled: bool = Field(
        ..., description="Whether the trusted-device duration override is active"
    )
    effective_trusted_expire_hours: int = Field(
        ..., description="The TTL trusted-device sessions actually get (hours)"
    )
    extend_on_activity: bool = Field(
        ..., description="Whether sessions re-arm on activity"
    )
    extend_threshold_hours: int = Field(
        ..., description="Re-arm threshold for untrusted sessions (hours)"
    )
    trusted_extend_threshold_hours: Optional[int] = Field(
        None, description="Re-arm threshold for trusted sessions (hours)"
    )


class TrustedDeviceEntry(BaseModel):
    """A known device with its trust state (no raw fingerprint is exposed)."""

    # Snowflake ids are strings: a JS Number cannot hold a 64-bit id exactly,
    # so a numeric field would round it and subsequent lookups would miss.
    id: str
    name: Optional[str] = None
    device_type: Optional[str] = None
    trusted: bool
    trusted_at: Optional[int] = None
    first_seen_at: int
    last_seen_at: int


class TrustedSessionEntry(BaseModel):
    """A live session with its effective duration and which policy applies."""

    id: str
    device_id: Optional[str] = None
    device_trusted: bool
    duration_hours: int
    policy_kind: str = Field(..., description="'trusted' or 'default'")
    created_at: int
    expires_at: int
    last_activity: int
    expired: bool


class DeviceTrustUserEntry(BaseModel):
    """Per-user device trust + session overview."""

    user_id: str
    devices: List[TrustedDeviceEntry] = []
    trusted_device_count: int
    sessions: List[TrustedSessionEntry] = []
    session_count: int
    active_session_count: int


class DeviceTrustTotals(BaseModel):
    """Aggregate counts across all users."""

    users: int
    devices: int
    trusted_devices: int
    sessions: int
    active_sessions: int
    trusted_device_sessions: int


class DeviceTrustOverviewResponse(BaseModel):
    """Full admin overview: policy, per-user rows, and totals."""

    policy: SessionDurationPolicy
    users: List[DeviceTrustUserEntry] = []
    totals: DeviceTrustTotals
