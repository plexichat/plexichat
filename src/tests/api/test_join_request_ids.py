"""Join request responses must serve ids as strings (like the list endpoint).

``_serialize_join`` already stringifies ``id`` because a JS ``Number``
cannot hold a 64-bit id exactly. ``POST /Join/request`` returned the raw
DB integer instead, so the client's ``Map`` keyed by ``Join_id`` saw both
``"1"`` (from the list) and ``1`` (from the create) for the same Join and
lookups/break calls missed.
"""

import pytest

import src.api.routes.join as join_module


class _FakeClient:
    host = "10.0.0.1"


class _FakeRequest:
    def __init__(self, body):
        self._body = body
        self.client = _FakeClient()
        self.headers = {}

    async def json(self):
        return self._body


class _FakeDB:
    def __init__(self, existing=None):
        self._existing = existing

    def fetch_one(self, query, params=None):
        return self._existing


class _FakeManager:
    def __init__(self, db, connection=None):
        self.db = db
        self._connection = connection

    def generate_shared_key(self):
        return "test-shared-key"

    def create_connection(self, **kwargs):
        return self._connection

    async def send_join_request(self, connection_id):
        return {"success": True}


def _patch(monkeypatch, manager):
    monkeypatch.setattr(join_module, "_require_manage", lambda request, user: 7)
    monkeypatch.setattr(join_module, "_get_manager", lambda request: manager)
    import src.core.plexijoin as plexijoin_core

    monkeypatch.setattr(plexijoin_core, "validate_remote_url", lambda url: None)


@pytest.mark.asyncio
async def test_request_join_create_serves_string_ids(monkeypatch):
    """The create path must stringify Join/connection ids."""
    manager = _FakeManager(_FakeDB(), connection={"id": 1234567890123456789})
    _patch(monkeypatch, manager)
    request = _FakeRequest({"remote_url": "https://remote.example"})

    result = await join_module.request_join(request, current_user=object())

    assert result["success"] is True
    assert result["Join_id"] == "1234567890123456789"
    assert result["connection_id"] == "1234567890123456789"
    assert result["id"] == "1234567890123456789"


@pytest.mark.asyncio
async def test_request_join_existing_serves_string_ids(monkeypatch):
    """The dedupe path must stringify Join/connection ids too."""
    manager = _FakeManager(_FakeDB(existing={"id": 42}))
    _patch(monkeypatch, manager)
    request = _FakeRequest({"remote_url": "https://remote.example"})

    result = await join_module.request_join(request, current_user=object())

    assert result["success"] is True
    assert result["existing"] is True
    assert result["Join_id"] == "42"
    assert result["connection_id"] == "42"
    assert result["id"] == "42"


def test_serialize_join_serves_string_ids():
    """The list shape this fix aligns with."""
    out = join_module._serialize_join(
        {
            "id": 1234567890123456789,
            "remote_instance_id": "remote:example",
            "remote_url": "https://remote.example",
            "status": "pending",
        }
    )
    assert out["Join_id"] == "1234567890123456789"
    assert out["id"] == "1234567890123456789"
