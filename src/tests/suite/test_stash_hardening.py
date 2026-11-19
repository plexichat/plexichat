"""Regression tests for PlexiStash metadata and link-URL hardening.

- Unbounded ``metadata`` was the one free-form field with no size/structure
  bound (title/description/tags are bounded by ``boundaries.py``). A client could
  persist arbitrarily large JSON that is decoded on every read, deeply nested
  structures that exhaust parser recursion, or control characters.
- Link item URLs were only checked for an http(s) scheme, so a renderer could be
  pointed at loopback/private/link-local endpoints (SSRF, e.g. cloud metadata).
"""

import pytest

from src.core.security.hardening import HardeningViolation
from src.core.suite.stash import StashManager
from src.core.suite.stash.manager import StashValidationError
from src.core.suite.stash.schema import create_tables as create_stash_tables


def _manager(db, server_manager, media_manager):
    create_stash_tables(db)
    return StashManager(db, servers_module=server_manager, media=media_manager)


class TestBoundedMetadata:
    def test_create_rejects_oversized_metadata(
        self, db, two_users, server_manager, media_manager
    ):
        owner, _ = two_users
        manager = _manager(db, server_manager, media_manager)
        with pytest.raises(StashValidationError):
            manager.create_item(
                owner.id,
                item_type="note",
                title="Huge",
                metadata={"blob": "x" * 100_000},
            )

    def test_create_rejects_deeply_nested_metadata(
        self, db, two_users, server_manager, media_manager
    ):
        owner, _ = two_users
        manager = _manager(db, server_manager, media_manager)
        nested = {"k": "leaf"}
        for _ in range(20):
            nested = {"k": nested}
        with pytest.raises(StashValidationError):
            manager.create_item(
                owner.id, item_type="note", title="Deep", metadata=nested
            )

    @pytest.mark.parametrize(
        "metadata",
        [
            "not-an-object",
            ["list", "instead", "of", "object"],
            {"bad": float("nan")},
            {"key\x00": "nul in key"},
            {"items": list(range(200))},
        ],
    )
    def test_create_rejects_malformed_metadata(
        self, db, two_users, server_manager, media_manager, metadata
    ):
        owner, _ = two_users
        manager = _manager(db, server_manager, media_manager)
        with pytest.raises(StashValidationError):
            manager.create_item(
                owner.id, item_type="note", title="Malformed", metadata=metadata
            )

    def test_update_rejects_oversized_metadata_and_keeps_old_value(
        self, db, two_users, server_manager, media_manager
    ):
        owner, _ = two_users
        manager = _manager(db, server_manager, media_manager)
        item = manager.create_item(
            owner.id, item_type="note", title="Note", metadata={"content": "ok"}
        )
        with pytest.raises(StashValidationError):
            manager.update_item(
                owner.id, int(item["id"]), metadata={"blob": "y" * 100_000}
            )
        assert manager.get_item(owner.id, int(item["id"]))["metadata"] == {
            "content": "ok"
        }

    def test_reasonable_metadata_still_roundtrips(
        self, db, two_users, server_manager, media_manager
    ):
        owner, _ = two_users
        manager = _manager(db, server_manager, media_manager)
        metadata = {
            "content_type": "text/plain",
            "size": 12,
            "url": "https://example.com",
        }
        item = manager.create_item(
            owner.id, item_type="link", title="Link", metadata=metadata
        )
        assert item["metadata"] == metadata


class TestWorkspaceFieldsBoundedOnUpdate:
    """Creation bounds title/description; the update path must not bypass them."""

    def test_update_rejects_oversized_description_and_keeps_old_value(
        self, db, two_users, server_manager, media_manager
    ):
        owner, _ = two_users
        manager = _manager(db, server_manager, media_manager)
        item = manager.create_item(
            owner.id, item_type="note", title="Note", description="keep me"
        )
        with pytest.raises(StashValidationError):
            manager.update_item(owner.id, int(item["id"]), description="x" * 100_000)
        assert manager.get_item(owner.id, int(item["id"]))["description"] == "keep me"

    @pytest.mark.parametrize(
        "field,value",
        [
            ("title", "safe\x00title"),
            ("title", "forged\n[SECURITY] audit line"),
            ("description", "desc\x1b[31mred"),
            ("description", "line1\r\nforged"),
        ],
    )
    def test_update_rejects_control_characters(
        self, db, two_users, server_manager, media_manager, field, value
    ):
        owner, _ = two_users
        manager = _manager(db, server_manager, media_manager)
        item = manager.create_item(
            owner.id, item_type="note", title="Note", description="clean"
        )
        # Control-character rejection surfaces as HardeningViolation (the
        # shared boundary helper has no error_cls hook for that check), the
        # same way it does on create_item.
        with pytest.raises(HardeningViolation):
            manager.update_item(owner.id, int(item["id"]), **{field: value})

    def test_update_accepts_normal_fields(
        self, db, two_users, server_manager, media_manager
    ):
        owner, _ = two_users
        manager = _manager(db, server_manager, media_manager)
        item = manager.create_item(owner.id, item_type="note", title="Note")
        updated = manager.update_item(
            owner.id,
            int(item["id"]),
            title="Renamed",
            description="A perfectly reasonable description",
        )
        assert updated["title"] == "Renamed"
        assert updated["description"] == "A perfectly reasonable description"


class TestLinkUrlSsrfGuard:
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1/admin",
            "http://localhost:8080/admin",
            "http://10.0.0.5/internal",
            "http://192.168.1.1/",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/admin",
            "http://[fd00::1]/",
            "http://0.0.0.0/",
            "http://metadata/latest/",
            "http://user:pass@example.com/",
        ],
    )
    def test_create_rejects_internal_link_hosts(
        self, db, two_users, server_manager, media_manager, url
    ):
        owner, _ = two_users
        manager = _manager(db, server_manager, media_manager)
        with pytest.raises(StashValidationError):
            manager.create_item(
                owner.id, item_type="link", title="L", metadata={"url": url}
            )

    def test_update_rejects_internal_link_host(
        self, db, two_users, server_manager, media_manager
    ):
        owner, _ = two_users
        manager = _manager(db, server_manager, media_manager)
        item = manager.create_item(
            owner.id,
            item_type="link",
            title="L",
            metadata={"url": "https://example.com/a"},
        )
        with pytest.raises(StashValidationError):
            manager.update_item(
                owner.id,
                int(item["id"]),
                metadata={"url": "http://169.254.169.254/latest/meta-data/"},
            )
        assert manager.get_item(owner.id, int(item["id"]))["metadata"]["url"] == (
            "https://example.com/a"
        )

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/a",
            "http://example.org/b",
            "https://sub.example.co.uk:8443/path?q=1",
        ],
    )
    def test_create_accepts_public_link_hosts(
        self, db, two_users, server_manager, media_manager, url
    ):
        owner, _ = two_users
        manager = _manager(db, server_manager, media_manager)
        item = manager.create_item(
            owner.id, item_type="link", title="L", metadata={"url": url}
        )
        assert item["metadata"]["url"] == url
