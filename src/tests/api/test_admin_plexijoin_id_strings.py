"""Admin PlexiJoin + federation inbound ids must be decimal strings.

The client ``/Join`` contract (``src/api/routes/join.py``) already
stringifies snowflake ids because a JS ``Number`` rounds 64-bit ids. The
admin ``/admin/plexijoin/*`` surface and the federation ``/join-request``
inbound route returned raw DB integers instead, splitting the client's
``Map`` keys (``"1"`` vs ``1``) for the same Join.
"""

import pytest

import src.api.routes.admin.plexijoin as admin_plexijoin
import src.api.routes.federation as federation_module


class _FakeClient:
    host = "10.0.0.1"


class _FakeRequest:
    def __init__(self, body=None):
        self._body = body or {}
        self.client = _FakeClient()
        self.headers = {}

    async def json(self):
        return self._body


class _FakeManager:
    def __init__(self, connection=None, requests=None, request_row=None):
        self._connection = connection
        self._requests = requests or {
            "requests": [],
            "total": 0,
            "page": 1,
            "per_page": 50,
            "pages": 0,
        }
        self._request_row = request_row

    def list_connections(self, **kwargs):
        return {
            "connections": [self._connection],
            "total": 1,
            "page": 1,
            "per_page": 50,
            "pages": 1,
        }

    def get_connection(self, connection_id):
        return self._connection

    def create_connection(self, **kwargs):
        return self._connection

    def list_requests(self, **kwargs):
        return self._requests

    def approve_request(self, **kwargs):
        return self._request_row

    def deny_request(self, **kwargs):
        return self._request_row


_BIG = 1234567890123456789


def _patch_admin(monkeypatch, manager):
    monkeypatch.setattr(admin_plexijoin, "check_host_restriction", lambda request: None)
    monkeypatch.setattr(admin_plexijoin, "get_admin_from_token", lambda request: 7)
    monkeypatch.setattr(
        admin_plexijoin, "require_admin_permission", lambda request, perm: 7
    )
    monkeypatch.setattr(
        admin_plexijoin, "_get_plexijoin_manager", lambda request: manager
    )


def _connection_row():
    return {
        "id": _BIG,
        "remote_instance_id": "remote:example",
        "remote_url": "https://remote.example",
        "status": "pending",
        "connected_at": None,
        "messages_in": 0,
        "messages_out": 0,
        "last_activity": None,
        "note": None,
        "created_at": 123,
        "created_by": 987654321098765432,
    }


def _request_row():
    return {
        "id": _BIG,
        "remote_instance_id": "remote:example",
        "remote_url": "https://remote.example",
        "requested_by": "someone",
        "note": None,
        "status": "approved",
        "requested_at": 123,
        "reviewed_at": 456,
        "reviewed_by": 777,
    }


@pytest.mark.asyncio
async def test_admin_list_connections_serves_string_ids(monkeypatch):
    _patch_admin(monkeypatch, _FakeManager(connection=_connection_row()))
    result = await admin_plexijoin.list_connections(_FakeRequest())
    conn = result["connections"][0]
    assert conn["id"] == str(_BIG)
    assert conn["created_by"] == "987654321098765432"


@pytest.mark.asyncio
async def test_admin_get_connection_serves_string_ids(monkeypatch):
    _patch_admin(monkeypatch, _FakeManager(connection=_connection_row()))
    result = await admin_plexijoin.get_connection(
        _FakeRequest(), connection_id=str(_BIG)
    )
    assert result["id"] == str(_BIG)
    assert result["created_by"] == "987654321098765432"


@pytest.mark.asyncio
async def test_admin_create_connection_serves_string_id(monkeypatch):
    _patch_admin(monkeypatch, _FakeManager(connection=_connection_row()))
    request = _FakeRequest(
        {
            "remote_instance_id": "remote:example",
            "remote_url": "https://remote.example",
            "shared_key": "sekret",
        }
    )
    result = await admin_plexijoin.create_connection(request)
    assert result["connection_id"] == str(_BIG)


@pytest.mark.asyncio
async def test_admin_list_requests_serves_string_ids(monkeypatch):
    row = _request_row()
    pending = dict(row, status="pending", reviewed_by=None, reviewed_at=None)
    manager = _FakeManager(
        requests={
            "requests": [pending],
            "total": 1,
            "page": 1,
            "per_page": 50,
            "pages": 1,
        }
    )
    _patch_admin(monkeypatch, manager)
    result = await admin_plexijoin.list_requests(_FakeRequest())
    req = result["requests"][0]
    assert req["id"] == str(_BIG)
    assert req["reviewed_by"] is None


@pytest.mark.asyncio
async def test_admin_approve_deny_serve_string_ids(monkeypatch):
    _patch_admin(monkeypatch, _FakeManager(request_row=_request_row()))
    for handler in (admin_plexijoin.approve_request, admin_plexijoin.deny_request):
        result = await handler(_FakeRequest(), request_id=str(_BIG))
        req = result["request"]
        assert req["id"] == str(_BIG)
        assert req["reviewed_by"] == "777"


def test_serialize_helpers_serve_string_ids():
    conn = admin_plexijoin._serialize_connection(_connection_row())
    assert conn["id"] == str(_BIG)
    req = admin_plexijoin._serialize_inbound_request(_request_row())
    assert req["id"] == str(_BIG)
    assert req["reviewed_by"] == "777"


@pytest.mark.asyncio
async def test_federation_join_request_inbound_serves_string_id(monkeypatch):
    manager = _FakeManager()

    async def _fake_body(request):
        return b'{"instance_id": "remote:example"}'

    class _InboundRequest:
        headers = {
            "x-plexijoin-signature": "sig",
            "x-plexijoin-timestamp": "1",
            "x-plexijoin-nonce": "n",
        }

        async def body(self):
            return await _fake_body(self)

    submitted = {}

    def _submit(remote_instance_id, remote_url, requested_by=None, note=None):
        submitted.update({"remote_instance_id": remote_instance_id})
        return dict(_request_row(), status="pending")

    manager.submit_inbound_request = _submit  # type: ignore[method-assign]
    monkeypatch.setattr(federation_module, "_get_plexijoin_manager", lambda: manager)
    monkeypatch.setattr(
        federation_module, "_authenticate", lambda *args, **kwargs: {"id": 1}
    )
    result = await federation_module.join_request_inbound(_InboundRequest())
    assert result["ok"] is True
    assert result["request_id"] == str(_BIG)
    assert submitted["remote_instance_id"] == "remote:example"
