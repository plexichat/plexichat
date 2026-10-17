"""PlexiStash / PlexiSum HTTP responses must carry ids as decimal strings.

``test_openapi_snowflake_ids`` asserts the *declared* schema, but a route can
still emit a numeric id at runtime if it bypasses its response model or a
``SnowflakeID`` serializer is dropped. These tests drive the real suite routes
end-to-end and walk the returned JSON: every ``id``/``*_id``/``*_ids`` value
must be a string (or a list of strings), and no integer at or above 2**53 may
appear anywhere — the threshold past which a JS ``Number`` loses precision.
"""

from typing import Any

import pytest

from src.core.artifacts.capabilities import CapabilityInfo, CapabilityState
from src.core.events.snowflakes import is_id_field

MIN_SNOWFLAKE = 2**53


def _available(feature: str) -> CapabilityInfo:
    return CapabilityInfo(feature, CapabilityState.AVAILABLE, "enabled")


@pytest.fixture
def suite_enabled(monkeypatch):
    """Gate PlexiStash and PlexiSum as AVAILABLE (tests run without a license)."""
    import src.api.routes.suite.stash as stash_routes
    import src.api.routes.suite.sum as sum_routes

    monkeypatch.setattr(stash_routes, "get_capability", _available)
    monkeypatch.setattr(sum_routes, "get_capability", _available)


def _string_id_violations(node: Any, path: str = "$") -> list[str]:
    """Return ``key path = value`` for every id field that is not a string."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}"
            if is_id_field(key):
                if value is None:
                    pass
                elif isinstance(value, list):
                    if not all(v is None or isinstance(v, str) for v in value):
                        found.append(f"{child} = {value!r} (expected list of strings)")
                elif not isinstance(value, str):
                    found.append(f"{child} = {value!r} (expected string)")
            found += _string_id_violations(value, child)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found += _string_id_violations(value, f"{path}[{index}]")
    return found


def _large_ints(node: Any, path: str = "$") -> list[str]:
    """Return ``key path = value`` for every int >= 2**53 in a payload."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}"
            if isinstance(value, bool):
                continue
            if isinstance(value, int) and abs(value) >= MIN_SNOWFLAKE:
                found.append(f"{child} = {value}")
            found += _large_ints(value, child)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found += _large_ints(value, f"{path}[{index}]")
    return found


def _check(payload: dict, where: str) -> None:
    assert not _string_id_violations(payload), (
        f"{where} returned a non-string id:\n"
        + "\n".join(_string_id_violations(payload))
    )
    assert not _large_ints(payload), (
        f"{where} returned a numeric snowflake id:\n" + "\n".join(_large_ints(payload))
    )


def test_helpers_flag_bad_ids_and_ignore_rowids():
    assert _string_id_violations({"id": 9007199254740993}) == [
        "$.id = 9007199254740993 (expected string)"
    ]
    assert _string_id_violations({"id": "9007199254740993"}) == []
    assert _string_id_violations({"subject_ids": ["1", "2"]}) == []
    assert _string_id_violations({"subject_ids": [1, 2]}) == [
        "$.subject_ids = [1, 2] (expected list of strings)"
    ]
    assert _large_ints({"nested": [{"user_id": 10**18}]}) == [
        "$.nested[0].user_id = 1000000000000000000"
    ]
    assert _large_ints({"id": "10", "ok": True}) == []


def test_stash_responses_use_string_ids(test_client, auth_headers, suite_enabled):
    headers = auth_headers

    item_resp = test_client.post(
        "/api/v1/suite/stash/items",
        headers=headers,
        json={
            "item_type": "note",
            "title": "Contract note",
            "metadata": {"content": "x"},
        },
    )
    assert item_resp.status_code == 201, item_resp.text
    _check(item_resp.json(), "POST /suite/stash/items")
    item_id = item_resp.json()["id"]
    assert isinstance(item_id, str) and item_id.isdigit()

    collection_resp = test_client.post(
        "/api/v1/suite/stash/collections",
        headers=headers,
        json={"name": "Contract collection"},
    )
    assert collection_resp.status_code == 201, collection_resp.text
    _check(collection_resp.json(), "POST /suite/stash/collections")

    for url in (
        "/api/v1/suite/stash/items",
        f"/api/v1/suite/stash/items/{item_id}",
        f"/api/v1/suite/stash/items/{item_id}/versions",
        "/api/v1/suite/stash/collections",
    ):
        resp = test_client.get(url, headers=headers)
        assert resp.status_code == 200, f"GET {url} -> {resp.status_code}: {resp.text}"
        _check(resp.json(), f"GET {url}")


def test_sum_responses_use_string_ids(test_client, auth_headers, suite_enabled):
    headers = auth_headers

    create_resp = test_client.post(
        "/api/v1/suite/sum/workbooks",
        headers=headers,
        json={"title": "Contract workbook", "scope": "personal"},
    )
    assert create_resp.status_code == 201, create_resp.text
    _check(create_resp.json(), "POST /suite/sum/workbooks")
    workbook = create_resp.json()
    workbook_id = workbook["id"]
    sheet_id = workbook["sheets"][0]["id"]
    assert isinstance(workbook_id, str) and isinstance(sheet_id, str)

    for url in (
        "/api/v1/suite/sum/workbooks",
        f"/api/v1/suite/sum/workbooks/{workbook_id}",
        f"/api/v1/suite/sum/workbooks/{workbook_id}/sheets/{sheet_id}/viewport",
        f"/api/v1/suite/sum/workbooks/{workbook_id}/operations",
        f"/api/v1/suite/sum/workbooks/{workbook_id}/snapshots",
        f"/api/v1/suite/sum/workbooks/{workbook_id}/grants",
    ):
        resp = test_client.get(url, headers=headers)
        assert resp.status_code == 200, f"GET {url} -> {resp.status_code}: {resp.text}"
        _check(resp.json(), f"GET {url}")
