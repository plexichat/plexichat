"""An untyped listing must not expose artifacts whose per-type capability is
unavailable.

The single-artifact GET and an explicit ``?artifact_type=...`` filter both call
``_require_artifact_capability`` for the artifact's type, so a whiteboard /
scribe / script artifact cannot be read once its license/config gate is off.
An untyped ``GET /artifacts`` skipped that gate entirely and shipped every
stored type -- including full payloads -- to any caller who passed the base
``artifacts`` capability and the scope check. That is a fail-open capability
bypass: the same record is a 403 by id and a 200 in the unfiltered list.

This regression drives the real ``list_artifacts`` route function with a stub
manager and a capability catalog where whiteboard is disabled, and asserts the
disabled-type artifact is absent from both the page and the total.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import src.api as api
from src.api.routes import artifacts_helpers as helpers
from src.api.routes.artifacts.routes import crud
from src.core.artifacts.capabilities import CapabilityInfo, CapabilityState
from src.core.artifacts.models import ArtifactStatus, ArtifactType

pytestmark = [pytest.mark.asyncio, pytest.mark.security, pytest.mark.unit]

_USER = 42


def _artifact(artifact_id: int, artifact_type: ArtifactType, payload: dict):
    return SimpleNamespace(
        id=artifact_id,
        conversation_id=None,
        channel_id=None,
        server_id=None,
        author_id=_USER,
        artifact_type=artifact_type,
        title="t",
        summary="sum",
        status=ArtifactStatus.COMPLETED,
        recorded=False,
        has_transcript=False,
        payload=payload,
        retention_policy=None,
        expires_at=None,
        license_feature=None,
        created_at=1,
        updated_at=1,
        rev=0,
    )


def _capability(feature: str, state: CapabilityState) -> CapabilityInfo:
    return CapabilityInfo(feature=feature, state=state, message="", details={})


async def test_untyped_list_excludes_artifacts_with_disabled_capability(monkeypatch):
    gated = _artifact(1001, ArtifactType.WHITEBOARD, {"board": {"secret": "s"}})
    allowed = _artifact(1002, ArtifactType.FILE, {"content": "ok"})
    stored = [gated, allowed]

    class _Manager:
        def list_with_filters(self, **kwargs):
            filters = kwargs.get("filters", {})
            items = stored
            permitted = filters.get("artifact_type")
            if permitted is not None:
                items = [a for a in items if a.artifact_type in permitted]
            offset = int(filters.get("offset", 0) or 0)
            limit = int(filters.get("limit", 50) or 50)
            return items[offset : offset + limit]

        def count(self, filters):
            permitted = filters.get("artifact_type")
            items = (
                stored
                if permitted is None
                else [a for a in stored if a.artifact_type in permitted]
            )
            return len(items)

    def _get_capability(feature, config=None):
        state = (
            CapabilityState.DISABLED_BY_LICENSE
            if feature == "artifacts_whiteboard"
            else CapabilityState.AVAILABLE
        )
        return _capability(feature, state)

    monkeypatch.setattr(helpers, "get_capability", _get_capability)
    monkeypatch.setattr(api, "get_db", lambda: object())
    monkeypatch.setattr(api, "get_servers", lambda: None)
    monkeypatch.setattr(api, "get_messaging", lambda: None)
    monkeypatch.setattr(api, "get_auth", lambda: None)
    monkeypatch.setattr(crud, "_get_manager", lambda: _Manager())
    monkeypatch.setattr(crud, "_authorize_scope", lambda *a, **k: None)
    monkeypatch.setattr(crud, "_prime_conversation_server_cache", lambda *a, **k: None)

    result = await crud.list_artifacts(
        current_user=SimpleNamespace(user_id=_USER, username="u"),
        include_payload=True,
        limit=50,
        offset=0,
        sort_by="created_at",
        sort_order="desc",
    )

    ids = [int(item.id) for item in result.items]
    assert 1002 in ids
    assert 1001 not in ids, "a disabled-capability artifact must not be listed"
    assert result.total == 1
