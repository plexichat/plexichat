"""Typed exception mapping for invite flows (W6-E verification).

Guards the InviteMaxUsesError -> proper HTTP status mapping so max-uses
exhaustion never surfaces as a 500 with str(e) leaked in the body.
"""

from __future__ import annotations

import pytest

from fastapi import HTTPException

from src.api.routes.exception_mapping import (
    map_invite_join_error,
    raise_mapped,
)
from src.core.servers import exceptions as srv


pytestmark = pytest.mark.unit


def test_invite_max_uses_join_flow_maps_to_400():
    exc = srv.InviteMaxUsesError(
        "Invite has reached maximum uses (max_uses=1, current_uses=1)",
        max_uses=1,
        current_uses=1,
    )
    with pytest.raises(HTTPException) as ei:
        map_invite_join_error(exc)
    assert ei.value.status_code == 410
    # Static message — no internal error text leaked.
    assert "max_uses" not in str(ei.value.detail)
    assert "1" not in str(ei.value.detail.get("error", {}).get("message", ""))
    assert ei.value.detail["error"]["message"] == "Invite is no longer usable"


def test_invite_max_uses_generic_mapping_maps_to_permission_family():
    exc = srv.InviteMaxUsesError("Invite has reached maximum uses")
    with pytest.raises(HTTPException) as ei:
        raise_mapped(exc)
    assert ei.value.status_code == 410


def test_invite_rate_limited_maps_to_429_with_retry_after():
    exc = srv.InviteRateLimitedError("Invite creation rate limit exceeded", 3600)
    with pytest.raises(HTTPException) as ei:
        raise_mapped(exc)
    assert ei.value.status_code == 429
    assert ei.value.headers.get("Retry-After") == "3600"


def test_invite_limit_reached_maps_to_400():
    exc = srv.InviteLimitReachedError("Active invite limit reached", limit=25)
    with pytest.raises(HTTPException) as ei:
        raise_mapped(exc)
    assert ei.value.status_code == 400
