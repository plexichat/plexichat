"""Package-boundary tests for the PlexiStash route split."""

from __future__ import annotations

import asyncio
import types

from fastapi.routing import APIRoute

from src.api.routes.suite import stash as pkg

HANDLERS = (
    "list_items",
    "create_item",
    "upload_file",
    "get_item",
    "update_item",
    "pin_item",
    "delete_item",
    "restore_item",
    "list_versions",
    "create_version",
    "grant_item",
    "create_download_ticket",
    "redeem_download_ticket",
    "revoke_grant",
    "list_collections",
    "create_collection",
    "grant_collection",
    "list_collection_grants",
    "revoke_collection_grant",
    "diff_versions",
    "create_share_link",
    "list_share_links",
    "revoke_share_link",
    "resolve_share_link",
    "share_link_download",
)


class TestCompatSurface:
    def test_router_is_the_registered_one(self) -> None:
        from src.api.routes import stash_router

        assert pkg.router is stash_router

    def test_every_handler_is_reexported(self) -> None:
        for name in HANDLERS:
            assert callable(getattr(pkg, name)), name

    def test_router_shape_is_unchanged(self) -> None:
        assert pkg.router.prefix == "/suite/stash"
        assert pkg.router.tags == ["PlexiStash"]
        routes = [route for route in pkg.router.routes if isinstance(route, APIRoute)]
        assert len(routes) == len(HANDLERS)


class TestPatchPoints:
    def test_manager_patch_reaches_the_item_handler(self, monkeypatch) -> None:
        captured: dict[str, object] = {}

        class FakeManager:
            def list_items(self, user_id, **kwargs):
                captured["user_id"] = user_id
                captured["search"] = kwargs.get("search")
                return [], 0

        monkeypatch.setattr(pkg, "_manager", lambda: FakeManager())
        asyncio.run(
            pkg.list_items(
                limit=10,
                offset=0,
                cursor=None,
                search="budget",
                scope=None,
                server_id=None,
                collection_id=None,
                pinned=None,
                current_user=types.SimpleNamespace(user_id=42),
            )
        )

        assert captured == {"user_id": 42, "search": "budget"}

    def test_capability_patch_reaches_the_helper(self, monkeypatch) -> None:
        state = types.SimpleNamespace(value="available")
        calls: list[str] = []

        def fake_capability(feature: str):
            calls.append(feature)
            return types.SimpleNamespace(state=state)

        monkeypatch.setattr(pkg, "get_capability", fake_capability)
        monkeypatch.setattr(pkg, "is_usable", lambda _state: True)

        pkg._require_stash_capability()

        assert calls == ["plexistash"]

    def test_upload_limits_are_read_from_the_package(self, monkeypatch) -> None:
        from src.api.routes.suite.stash import uploads

        monkeypatch.setattr(pkg, "MAX_UPLOAD_DECODED_BYTES", 100)
        monkeypatch.setattr(pkg, "MAX_UPLOAD_ENCODED_LEN", 200)

        assert uploads._limits() == (200, 100)
