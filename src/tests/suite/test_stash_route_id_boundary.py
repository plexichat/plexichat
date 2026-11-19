"""Regression: PlexiStash route ids must use the canonical ASCII snowflake gate.

The Stash route handlers used bare ``int(raw)`` on path/query ids. ``int`` accepts
Unicode decimal homoglyphs (``"١٢٣"``, ``"１２３"``), PEP 515 underscores
(``"1_2"``), signs and arbitrarily large magnitudes. Every other route boundary
funnels ids through the ASCII-only, 64-bit bounded ``parse_id`` guard, so a
homoglyph could be read as a different id by an upstream ASCII-only validator
(or another language's digit matcher), and an oversized value reached SQL
bindings as an ``OverflowError``-inducing 500 instead of a clean 400.

These tests drive the route handlers directly (with a recording manager) and
assert the permissive spellings are rejected with 400 before any manager call.
"""

from __future__ import annotations

import asyncio
import types

import pytest
from fastapi import HTTPException

from src.api.routes.suite import stash as pkg
from src.api.routes.suite.stash import collections, item_detail, share_links, version_diff

USER = types.SimpleNamespace(user_id=42)

# Spellings ``int()`` accepts but the canonical snowflake gate rejects.
NON_CANONICAL_IDS = (
    "١٢٣",  # Arabic-Indic digits (value 123)
    "１２３",  # fullwidth digits (value 123)
    "1_2",  # PEP 515 underscore separator (value 12)
    "-5",  # negative
    "0",  # zero is not a snowflake
    "99999999999999999999999",  # past the 64-bit ceiling
)


class RecordingManager:
    """Records method calls; invalid ids must fail before any method runs."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def _record(self, name: str, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        return {}

    def get_item(self, *args, **kwargs):
        return self._record("get_item", *args, **kwargs)

    def create_share_link(self, *args, **kwargs):
        return self._record("create_share_link", *args, **kwargs)

    def diff_versions(self, *args, **kwargs):
        return self._record("diff_versions", *args, **kwargs)

    def list_collections(self, *args, **kwargs):
        return self._record("list_collections", *args, **kwargs)


@pytest.fixture
def manager(monkeypatch) -> RecordingManager:
    rec = RecordingManager()
    monkeypatch.setattr(pkg, "_manager", lambda: rec)
    return rec


@pytest.mark.parametrize("raw", NON_CANONICAL_IDS)
def test_get_item_rejects_non_canonical_ids(manager: RecordingManager, raw: str) -> None:
    with pytest.raises(HTTPException) as exc:
        asyncio.run(item_detail.get_item(raw, current_user=USER))
    assert exc.value.status_code == 400
    assert manager.calls == [], "manager must not be reached with a non-canonical id"


def test_get_item_accepts_ascii_snowflake(monkeypatch, manager: RecordingManager) -> None:
    # Bypass only response serialization; the id boundary under test is intact.
    monkeypatch.setattr(item_detail, "_item_response", lambda item: item)
    result = asyncio.run(item_detail.get_item("123", current_user=USER))
    assert manager.calls == [("get_item", (42, 123), {})]
    assert result == {}


@pytest.mark.parametrize("raw", NON_CANONICAL_IDS)
def test_share_link_create_rejects_non_canonical_item_id(
    manager: RecordingManager, raw: str
) -> None:
    with pytest.raises(HTTPException) as exc:
        asyncio.run(share_links.create_share_link(raw, body=None, current_user=USER))
    assert exc.value.status_code == 400
    assert manager.calls == []


@pytest.mark.parametrize("raw", NON_CANONICAL_IDS)
def test_version_diff_rejects_non_canonical_ids(
    manager: RecordingManager, raw: str
) -> None:
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            version_diff.diff_versions("10", a=raw, b=None, current_user=USER)
        )
    assert exc.value.status_code == 400
    assert manager.calls == []


@pytest.mark.parametrize("raw", NON_CANONICAL_IDS)
def test_collection_list_rejects_non_canonical_server_id(
    manager: RecordingManager, raw: str
) -> None:
    with pytest.raises(HTTPException) as exc:
        asyncio.run(collections.list_collections(server_id=raw, current_user=USER))
    assert exc.value.status_code == 400
    assert manager.calls == []
