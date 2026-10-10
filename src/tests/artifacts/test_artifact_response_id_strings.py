"""PlexiScribe artifact HTTP responses must carry ids as decimal strings.

PlexiScribe persists a document as an ``artifact`` (the client's
``updateArtifact``/``getArtifactOps`` calls), so the artifacts REST surface is
its server contract. This drives the real routes end-to-end and walks the JSON:
every ``id``/``*_id``/``*_ids`` value must be a string, and no integer may
reach 2**53 — the threshold past which a JS ``Number`` loses precision.
"""

import uuid
from typing import Any
from unittest.mock import patch

import pytest

from src.core.artifacts.capabilities import CapabilityInfo, CapabilityState
from src.core.events.snowflakes import is_id_field
from src.utils import encryption

MIN_SNOWFLAKE = 2**53


@pytest.fixture
def scribe_enabled(monkeypatch):
    """Gate PlexiScribe artifacts as AVAILABLE (tests run without a license)."""
    import src.api.routes.artifacts_helpers as helpers

    def _available(feature, _cfg=None):
        return CapabilityInfo(feature, CapabilityState.AVAILABLE, "enabled")

    monkeypatch.setattr(helpers, "get_capability", _available)


def _register_and_login(auth_manager, label="scribe"):
    unique = uuid.uuid4().hex[:8]
    username = f"{label}_{unique}"
    email = f"{label}_{unique}@test.local"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        auth_manager.register(username=username, email=email, password="TestPass123!")
    with patch.object(encryption, "verify_password", return_value=True):
        result = auth_manager.login(username, "TestPass123!")
    return result.token


def _string_id_violations(node: Any, path: str = "$") -> list[str]:
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
    violations = _string_id_violations(payload)
    assert not violations, f"{where} returned a non-string id:\n" + "\n".join(
        violations
    )
    large = _large_ints(payload)
    assert not large, f"{where} returned a numeric snowflake id:\n" + "\n".join(large)


def test_scanner_flags_bad_ids_and_ignores_rowids():
    assert _string_id_violations({"id": 9007199254740993}) == [
        "$.id = 9007199254740993 (expected string)"
    ]
    assert _string_id_violations({"id": "1"}) == []
    assert _large_ints({"nested": [{"author_id": 10**18}]}) == [
        "$.nested[0].author_id = 1000000000000000000"
    ]
    assert _large_ints({"ok": True, "n": 42}) == []


class TestArtifactResponseStringIds:
    @pytest.fixture
    def headers(self, auth_manager):
        return {"Authorization": f"Bearer {_register_and_login(auth_manager)}"}

    def test_create_get_patch_and_ops_carry_string_ids(
        self, test_client, headers, scribe_enabled
    ):
        created = test_client.post(
            "/api/v1/artifacts",
            json={
                "artifact_type": "plexiscribe",
                "title": "Scribe contract",
                "payload": {"document": {"content_html": "<p>hi</p>"}},
            },
            headers=headers,
        )
        assert created.status_code == 200, created.text
        _check(created.json(), "POST /artifacts")
        artifact_id = created.json()["id"]
        assert isinstance(artifact_id, str) and artifact_id.isdigit()
        assert isinstance(created.json()["author_id"], str)

        listed = test_client.get("/api/v1/artifacts", headers=headers)
        assert listed.status_code == 200, listed.text
        _check(listed.json(), "GET /artifacts")

        fetched = test_client.get(f"/api/v1/artifacts/{artifact_id}", headers=headers)
        assert fetched.status_code == 200, fetched.text
        _check(fetched.json(), f"GET /artifacts/{artifact_id}")

        ops = test_client.get(f"/api/v1/artifacts/{artifact_id}/ops", headers=headers)
        assert ops.status_code == 200, ops.text
        _check(ops.json(), f"GET /artifacts/{artifact_id}/ops")

        patched = test_client.patch(
            f"/api/v1/artifacts/{artifact_id}",
            json={"payload": {"document": {"content_html": "<p>edited</p>"}, "rev": 1}},
            headers=headers,
        )
        assert patched.status_code == 200, patched.text
        _check(patched.json(), f"PATCH /artifacts/{artifact_id}")

    def test_path_param_accepts_decimal_string_id(self, test_client, headers):
        created = test_client.post(
            "/api/v1/artifacts",
            json={"artifact_type": "file", "title": "Param form"},
            headers=headers,
        )
        assert created.status_code == 200, created.text
        artifact_id = created.json()["id"]

        as_string = test_client.get(f"/api/v1/artifacts/{artifact_id}", headers=headers)
        as_int = test_client.get(
            f"/api/v1/artifacts/{int(artifact_id)}", headers=headers
        )
        assert as_string.status_code == as_int.status_code == 200, (
            as_string.text,
            as_int.text,
        )
        assert as_string.json()["id"] == str(artifact_id)

    def test_list_filters_accept_string_ids(self, test_client, headers):
        created = test_client.post(
            "/api/v1/artifacts",
            json={"artifact_type": "file", "title": "Filter form"},
            headers=headers,
        )
        assert created.status_code == 200, created.text
        author_id = created.json()["author_id"]

        filtered = test_client.get(
            "/api/v1/artifacts",
            params={"author_id": f"{author_id}"},
            headers=headers,
        )
        assert filtered.status_code == 200, filtered.text
        assert filtered.json()["items"], "string author_id filter returned nothing"
        _check(filtered.json(), "GET /artifacts?author_id=<string>")
