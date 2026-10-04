"""Admin endpoints that echo a snowflake id must serialize it as a string.

``POST /admin/search/reindex/conversation/{id}`` echoed its int-typed path
parameter and ``GET/POST /admin/artifacts/features/{feature}`` echoed an
int-typed ``server_id``. Both are 64-bit snowflakes, which a JS Number rounds
above 2^53, so the responses now carry decimal strings (project convention).
"""

import asyncio
from types import SimpleNamespace

BIG = 9007199254740993  # past 2^53: a JSON number would lose precision


def test_reindex_conversation_stringifies_conversation_id(monkeypatch):
    from src.api.routes.admin import reindex as reindex_routes

    class _Manager:
        def reindex_conversation(self, conversation_id, force):
            assert conversation_id == BIG
            return 3

    monkeypatch.setattr(reindex_routes, "check_host_restriction", lambda request: None)
    monkeypatch.setattr(
        reindex_routes, "require_admin_permission", lambda request, perm: 1
    )
    monkeypatch.setattr(reindex_routes, "_require_search_ready", lambda: None)
    monkeypatch.setattr(reindex_routes.search_module, "_manager", _Manager())

    body = asyncio.run(
        reindex_routes.reindex_conversation(
            request=SimpleNamespace(), conversation_id=BIG, body=None
        )
    )

    assert body["conversation_id"] == str(BIG)
    assert isinstance(body["conversation_id"], str)
    # Non-id numbers stay numeric.
    assert body["indexed"] == 3


def _artifact_manager():
    class _Manager:
        def get_feature_settings(self, server_id, feature):
            return {"enabled": True}

        def set_feature_settings_bulk(self, server_id, feature, settings):
            return {"enabled": True}

    return _Manager()


def test_get_feature_settings_stringifies_server_id(monkeypatch):
    from src.api.routes.admin import artifacts as artifact_routes

    monkeypatch.setattr(artifact_routes, "check_host_restriction", lambda request: None)
    monkeypatch.setattr(artifact_routes, "get_admin_from_token", lambda request: 1)
    monkeypatch.setattr(artifact_routes, "_get_manager", _artifact_manager)

    body = asyncio.run(
        artifact_routes.admin_get_feature_settings(
            feature="voice", server_id=BIG, request=SimpleNamespace()
        )
    )

    assert body["server_id"] == str(BIG)
    assert isinstance(body["server_id"], str)
    assert body["settings"] == {"enabled": True}


def test_set_feature_settings_stringifies_server_id(monkeypatch):
    from src.api.routes.admin import artifacts as artifact_routes

    monkeypatch.setattr(artifact_routes, "check_host_restriction", lambda request: None)
    monkeypatch.setattr(
        artifact_routes, "require_admin_permission", lambda request, perm: 1
    )
    monkeypatch.setattr(artifact_routes, "_get_manager", _artifact_manager)

    body = asyncio.run(
        artifact_routes.admin_set_feature_settings(
            feature="voice",
            body={"server_id": BIG, "settings": {}},
            request=SimpleNamespace(),
        )
    )

    assert body["server_id"] == str(BIG)
    assert isinstance(body["server_id"], str)
